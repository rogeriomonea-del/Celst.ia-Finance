"use client";

/**
 * Aba "Importar" de extratos e faturas.
 *
 * Fluxo: formulário obrigatório (tipo de documento + instituição + conta OU
 * cartão, período e senha de PDF opcionais) → dropzone → processamento com
 * passos anunciados (`aria-live`) → PRÉVIA completa (cards, amostra, listas
 * expansíveis de pendências) → confirmar ou descartar. Quando o parser não
 * reconhece as colunas, entra o MAPEADOR (select por coluna → novo parse).
 * Abaixo, a lista de arquivos/lotes já enviados com ações por status.
 *
 * Dinheiro chega em centavos int e só vira "R$" na formatação
 * (`formatar-centavos.ts`). Nada aqui faz conta com float.
 */

import {
  useCallback,
  useEffect,
  useId,
  useMemo,
  useRef,
  useState,
  type DragEvent,
  type KeyboardEvent,
  type ReactNode,
} from "react";
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ChevronDown,
  CreditCard,
  Download,
  FileText,
  Landmark,
  Loader2,
  Plus,
  RefreshCw,
  Trash2,
  Undo2,
  UploadCloud,
} from "lucide-react";
import type {
  ArquivoImportado,
  MapeamentoNecessario,
  PreviaImportacao,
  Transacao,
} from "@/lib/types-extratos";
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
import {
  ErroApiExtratos,
  aplicarMapeamento,
  confirmarLote,
  enviarArquivo,
  listarArquivos,
  listarCartoes,
  listarContas,
  listarInstituicoes,
  reprocessarArquivo,
  reverterLote,
  urlDownloadOriginal,
} from "@/components/extratos/api";
import {
  centavosOuTraco,
  formatarCentavos,
  formatarDataHoraISO,
  formatarDataISO,
  formatarInteiro,
  textoOuTraco,
} from "@/components/extratos/formatar-centavos";

// --------------------------------------------------------------------------
// Vocabulários da aba
// --------------------------------------------------------------------------

/** Extensões que o pipeline de extratos aceita (`engine/extratos/detect.py`). */
export const EXTENSOES_EXTRATO = [
  ".csv",
  ".xlsx",
  ".xls",
  ".ofx",
  ".qfx",
  ".xml",
  ".pdf",
] as const;

type TipoDocumentoUpload = "extrato_conta" | "fatura_cartao";

const ROTULOS_TIPO_DOCUMENTO: Record<string, string> = {
  extrato_conta: "Extrato de conta",
  fatura_cartao: "Fatura de cartão",
  planilha_config: "Planilha de config.",
};

type VarianteBadge =
  | "default"
  | "secondary"
  | "destructive"
  | "outline"
  | "warning"
  | "muted";

const STATUS_ARQUIVO: Record<string, { rotulo: string; variante: VarianteBadge }> = {
  recebido: { rotulo: "Recebido", variante: "muted" },
  validando: { rotulo: "Validando", variante: "secondary" },
  processando: { rotulo: "Processando", variante: "secondary" },
  aguardando_revisao: { rotulo: "Aguardando revisão", variante: "warning" },
  pronto: { rotulo: "Prévia pronta", variante: "outline" },
  confirmado: { rotulo: "Confirmado", variante: "default" },
  falhou: { rotulo: "Falhou", variante: "destructive" },
  revertido: { rotulo: "Revertido", variante: "muted" },
};

/** Papéis que uma coluna pode assumir no mapeador manual. */
const PAPEIS_COLUNA = [
  "data",
  "descricao",
  "valor",
  "debito",
  "credito",
  "saldo",
  "documento",
  "id_banco",
  "ignorar",
] as const;

type PapelColuna = (typeof PAPEIS_COLUNA)[number];

const ROTULOS_PAPEL: Record<PapelColuna, string> = {
  data: "Data",
  descricao: "Descrição",
  valor: "Valor (com sinal)",
  debito: "Débito",
  credito: "Crédito",
  saldo: "Saldo",
  documento: "Documento",
  id_banco: "ID do banco",
  ignorar: "Ignorar coluna",
};

// --------------------------------------------------------------------------
// Utilidades locais
// --------------------------------------------------------------------------

/** Comparação case/acento-insensível (mesma da busca do motor). */
function normalizar(texto: string): string {
  return texto
    .toLowerCase()
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .trim();
}

function extensaoValida(nome: string, extensoes: readonly string[]): boolean {
  const minusculo = (nome || "").toLowerCase();
  return extensoes.some((extensao) => minusculo.endsWith(extensao));
}

/** Tamanho legível: 1234 → "1,2 KB" (não é dinheiro; float aqui é ok). */
function formatarTamanho(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1).replace(".", ",")} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1).replace(".", ",")} MB`;
}

function mensagemDeFalha(falha: unknown, padrao: string): string {
  if (falha instanceof ErroApiExtratos) return falha.message;
  if (falha instanceof Error && falha.message.trim() !== "") return falha.message;
  return padrao;
}

/** A lista de arquivos pode vir com o lote mais recente anexado pela API. */
type ArquivoComLote = ArquivoImportado & {
  lote_id?: number | null;
  ultimo_lote_id?: number | null;
};

function loteDoArquivo(arquivo: ArquivoComLote): number | null {
  return arquivo.ultimo_lote_id ?? arquivo.lote_id ?? null;
}

// --------------------------------------------------------------------------
// Combobox pesquisável com "criar novo"
// --------------------------------------------------------------------------

export interface OpcaoCombobox {
  nome: string;
  detalhe?: string | null;
}

interface ComboboxCriavelProps {
  id: string;
  rotulo: string;
  /** Ex.: "instituição", "conta", "cartão" — usado no item "criar". */
  substantivo: string;
  /** true → "Criar nova …"; false → "Criar novo …". */
  feminino?: boolean;
  valor: string;
  onChange: (novo: string) => void;
  opcoes: OpcaoCombobox[];
  placeholder?: string;
  desabilitado?: boolean;
  obrigatorio?: boolean;
}

/**
 * Combobox acessível: digitação livre filtra as opções vindas da API e, quando
 * o texto não existe no cadastro, aparece "Criar novo …" — o valor digitado é
 * enviado como nome e o motor cria o registro na importação.
 */
function ComboboxCriavel({
  id,
  rotulo,
  substantivo,
  feminino = false,
  valor,
  onChange,
  opcoes,
  placeholder,
  desabilitado = false,
  obrigatorio = false,
}: ComboboxCriavelProps) {
  const [aberto, setAberto] = useState(false);
  const [destaque, setDestaque] = useState(0);
  const idLista = `${id}-lista`;

  const filtradas = useMemo(() => {
    const chave = normalizar(valor);
    if (chave === "") return opcoes;
    return opcoes.filter((opcao) => normalizar(opcao.nome).includes(chave));
  }, [opcoes, valor]);

  const existeExata = useMemo(
    () => opcoes.some((opcao) => normalizar(opcao.nome) === normalizar(valor)),
    [opcoes, valor]
  );

  const mostrarCriar = valor.trim() !== "" && !existeExata;
  const totalItens = filtradas.length + (mostrarCriar ? 1 : 0);

  const selecionar = useCallback(
    (indice: number) => {
      if (indice < filtradas.length) {
        onChange(filtradas[indice].nome);
      } else {
        onChange(valor.trim());
      }
      setAberto(false);
    },
    [filtradas, onChange, valor]
  );

  const aoTeclar = useCallback(
    (evento: KeyboardEvent<HTMLInputElement>) => {
      if (evento.key === "ArrowDown") {
        evento.preventDefault();
        if (!aberto) setAberto(true);
        setDestaque((atual) => Math.min(atual + 1, Math.max(totalItens - 1, 0)));
      } else if (evento.key === "ArrowUp") {
        evento.preventDefault();
        setDestaque((atual) => Math.max(atual - 1, 0));
      } else if (evento.key === "Enter") {
        if (aberto && totalItens > 0) {
          evento.preventDefault();
          selecionar(Math.min(destaque, totalItens - 1));
        }
      } else if (evento.key === "Escape") {
        setAberto(false);
      }
    },
    [aberto, destaque, selecionar, totalItens]
  );

  return (
    <div className="relative flex min-w-0 flex-col gap-1">
      <label
        htmlFor={id}
        className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground"
      >
        {rotulo}
        {obrigatorio && (
          <span className="text-loss" aria-hidden>
            {" "}
            *
          </span>
        )}
      </label>
      <Input
        id={id}
        role="combobox"
        aria-expanded={aberto}
        aria-controls={idLista}
        aria-autocomplete="list"
        aria-required={obrigatorio}
        aria-activedescendant={
          aberto && totalItens > 0 ? `${id}-opcao-${Math.min(destaque, totalItens - 1)}` : undefined
        }
        autoComplete="off"
        value={valor}
        placeholder={placeholder ?? "Digite para buscar ou criar"}
        disabled={desabilitado}
        onChange={(evento) => {
          onChange(evento.target.value);
          setAberto(true);
          setDestaque(0);
        }}
        onFocus={() => setAberto(true)}
        onBlur={() => setAberto(false)}
        onKeyDown={aoTeclar}
      />
      {aberto && totalItens > 0 && (
        <ul
          id={idLista}
          role="listbox"
          aria-label={`Opções de ${rotulo}`}
          className="absolute top-full z-30 mt-1 max-h-56 w-full overflow-y-auto rounded-xl border border-white/10 bg-surface-raised py-1 shadow-lg scrollbar-thin"
        >
          {filtradas.map((opcao, indice) => (
            <li
              key={opcao.nome}
              id={`${id}-opcao-${indice}`}
              role="option"
              aria-selected={normalizar(opcao.nome) === normalizar(valor)}
              className={cn(
                "flex cursor-pointer items-baseline gap-2 px-3 py-1.5 text-sm",
                indice === destaque ? "bg-primary/15 text-foreground" : "text-foreground"
              )}
              onMouseDown={(evento) => {
                evento.preventDefault();
                selecionar(indice);
              }}
              onMouseEnter={() => setDestaque(indice)}
            >
              <span className="truncate">{opcao.nome}</span>
              {opcao.detalhe && (
                <span className="truncate text-xs text-muted-foreground">
                  {opcao.detalhe}
                </span>
              )}
            </li>
          ))}
          {mostrarCriar && (
            <li
              id={`${id}-opcao-${filtradas.length}`}
              role="option"
              aria-selected={false}
              className={cn(
                "flex cursor-pointer items-center gap-2 border-t border-white/10 px-3 py-1.5 text-sm text-primary",
                destaque === filtradas.length && "bg-primary/15"
              )}
              onMouseDown={(evento) => {
                evento.preventDefault();
                selecionar(filtradas.length);
              }}
              onMouseEnter={() => setDestaque(filtradas.length)}
            >
              <Plus className="h-3.5 w-3.5 shrink-0" aria-hidden />
              Criar {feminino ? "nova" : "novo"} {substantivo} “{valor.trim()}”
            </li>
          )}
        </ul>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Dropzone genérica (reusada pela aba Config)
// --------------------------------------------------------------------------

export interface DropzoneArquivoProps {
  id: string;
  extensoes: readonly string[];
  onArquivo: (arquivo: File) => void;
  onErro: (mensagem: string) => void;
  processando?: boolean;
  desabilitado?: boolean;
  /** Aparece quando o envio está bloqueado (ex.: formulário incompleto). */
  motivoBloqueio?: string;
  titulo?: string;
  descricao?: string;
}

/** Área de arrastar/clicar no mesmo desenho da dropzone de `/importar`. */
export function DropzoneArquivo({
  id,
  extensoes,
  onArquivo,
  onErro,
  processando = false,
  desabilitado = false,
  motivoBloqueio,
  titulo = "Arraste o arquivo aqui ou clique para escolher",
  descricao,
}: DropzoneArquivoProps) {
  const [arrastando, setArrastando] = useState(false);
  const inativo = processando || desabilitado;

  const receber = useCallback(
    (arquivo: File | undefined | null) => {
      if (!arquivo) return;
      if (!extensaoValida(arquivo.name, extensoes)) {
        onErro(
          `“${arquivo.name}” não tem um formato aceito. Envie um arquivo ${extensoes.join(", ")}.`
        );
        return;
      }
      onArquivo(arquivo);
    },
    [extensoes, onArquivo, onErro]
  );

  const soltar = useCallback(
    (evento: DragEvent<HTMLLabelElement>) => {
      evento.preventDefault();
      setArrastando(false);
      if (inativo) return;
      receber(evento.dataTransfer.files?.[0]);
    },
    [inativo, receber]
  );

  return (
    <div className="w-full">
      <input
        id={id}
        name="file"
        type="file"
        accept={extensoes.join(",")}
        disabled={inativo}
        className="peer sr-only"
        onChange={(evento) => {
          receber(evento.target.files?.[0]);
          evento.target.value = "";
        }}
      />
      <label
        htmlFor={id}
        onDragOver={(evento) => {
          evento.preventDefault();
          if (!inativo) setArrastando(true);
        }}
        onDragLeave={() => setArrastando(false)}
        onDrop={soltar}
        className={cn(
          "group flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed px-6 py-10 text-center transition-colors",
          "peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-background",
          processando && "cursor-progress border-white/10 bg-surface opacity-80",
          desabilitado && !processando && "cursor-not-allowed border-white/10 bg-surface opacity-60",
          !inativo && "cursor-pointer",
          arrastando
            ? "border-primary/60 bg-primary/5"
            : !inativo && "border-white/15 bg-surface hover:border-white/30 hover:bg-white/[0.02]"
        )}
      >
        <span
          className={cn(
            "flex h-12 w-12 items-center justify-center rounded-2xl ring-1 transition-colors",
            arrastando
              ? "bg-primary/15 text-primary ring-primary/30"
              : "bg-white/5 text-muted-foreground ring-white/10 group-hover:text-foreground"
          )}
          aria-hidden
        >
          {processando ? (
            <Loader2 className="h-5 w-5 animate-spin" />
          ) : (
            <UploadCloud className="h-5 w-5" />
          )}
        </span>
        <span className="block space-y-1">
          <span className="block text-sm font-medium">
            {processando ? "Enviando o arquivo…" : titulo}
          </span>
          <span className="block text-xs text-muted-foreground">
            {descricao ?? `Formatos aceitos: ${extensoes.join(", ")} (até 15 MB)`}
          </span>
          {desabilitado && !processando && motivoBloqueio && (
            <span className="block text-xs text-amber-400">{motivoBloqueio}</span>
          )}
        </span>
      </label>
    </div>
  );
}

// --------------------------------------------------------------------------
// Estado de processamento (passos anunciados por aria-live)
// --------------------------------------------------------------------------

const PASSOS_IMPORTACAO = [
  "Validando e guardando o original",
  "Detectando o formato do documento",
  "Normalizando as transações",
  "Montando a prévia",
] as const;

/** Quando cada passo entra em cena (ms) — o último gira até a resposta. */
const MARCOS_IMPORTACAO_MS = [900, 2200, 3800] as const;

function EstadoProcessando({ nomeArquivo }: { nomeArquivo: string | null }) {
  const [passoAtual, setPassoAtual] = useState(0);

  useEffect(() => {
    const relogios = MARCOS_IMPORTACAO_MS.map((atraso, indice) =>
      setTimeout(() => setPassoAtual(indice + 1), atraso)
    );
    return () => relogios.forEach(clearTimeout);
  }, []);

  return (
    <Card>
      <CardContent
        className="space-y-4 p-6"
        role="status"
        aria-live="polite"
        aria-busy="true"
      >
        <p className="text-sm font-medium">
          Processando {nomeArquivo ? `“${nomeArquivo}”` : "o arquivo"}…
        </p>
        <ol className="space-y-2.5">
          {PASSOS_IMPORTACAO.map((passo, indice) => {
            const concluido = indice < passoAtual;
            const ativo = indice === passoAtual;
            return (
              <li
                key={passo}
                className={cn(
                  "flex items-center gap-3 text-sm transition-colors",
                  concluido && "text-muted-foreground",
                  ativo && "text-foreground",
                  !concluido && !ativo && "text-muted-foreground/50"
                )}
              >
                <span
                  className={cn(
                    "flex h-6 w-6 shrink-0 items-center justify-center rounded-lg ring-1",
                    concluido && "bg-profit/10 text-profit ring-profit/20",
                    ativo && "bg-primary/10 text-primary ring-primary/25",
                    !concluido && !ativo && "bg-white/5 text-muted-foreground ring-white/10"
                  )}
                  aria-hidden
                >
                  {concluido ? (
                    <Check className="h-3.5 w-3.5" />
                  ) : ativo ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <span className="h-1.5 w-1.5 rounded-full bg-current" />
                  )}
                </span>
                <span>{passo}</span>
              </li>
            );
          })}
        </ol>
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Lista expansível (duplicidades, erros, avisos…)
// --------------------------------------------------------------------------

interface ListaExpansivelProps {
  titulo: string;
  tom: "ambar" | "carmim";
  itens: ReactNode[];
  abertaInicialmente?: boolean;
}

/** Bloco recolhível com contagem — âmbar para revisão, carmim para erro. */
export function ListaExpansivel({
  titulo,
  tom,
  itens,
  abertaInicialmente = false,
}: ListaExpansivelProps) {
  const [aberta, setAberta] = useState(abertaInicialmente);
  const idConteudo = useId();
  if (itens.length === 0) return null;

  const carmim = tom === "carmim";
  return (
    <div
      className={cn(
        "rounded-xl border px-4 py-3",
        carmim ? "border-loss/30 bg-loss/[0.07]" : "border-amber-500/20 bg-amber-500/[0.06]"
      )}
    >
      <button
        type="button"
        aria-expanded={aberta}
        aria-controls={idConteudo}
        onClick={() => setAberta((atual) => !atual)}
        className={cn(
          "flex w-full items-center gap-2 text-left text-sm font-medium",
          carmim ? "text-loss" : "text-amber-300"
        )}
      >
        <AlertTriangle className="h-4 w-4 shrink-0" aria-hidden />
        <span className="min-w-0 flex-1">
          {titulo} ({formatarInteiro(itens.length)})
        </span>
        <ChevronDown
          className={cn("h-4 w-4 shrink-0 transition-transform", aberta && "rotate-180")}
          aria-hidden
        />
      </button>
      {aberta && (
        <ul id={idConteudo} className="mt-2 space-y-1 pl-6">
          {itens.map((item, indice) => (
            <li
              key={indice}
              className={cn(
                "list-disc text-xs",
                carmim ? "text-loss/90" : "text-amber-300/90"
              )}
            >
              {item}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Mapeador de colunas
// --------------------------------------------------------------------------

interface MapeadorColunasProps {
  mapeamento: MapeamentoNecessario;
  aplicando: boolean;
  onAplicar: (atribuicoes: Record<string, string>) => void;
}

/** Sugestões podem vir papel→coluna ou coluna→papel; aceita as duas. */
function atribuicoesIniciais(mapeamento: MapeamentoNecessario): Record<string, PapelColuna> {
  const iniciais: Record<string, PapelColuna> = {};
  for (const coluna of mapeamento.colunas) iniciais[coluna] = "ignorar";
  const papeis = new Set<string>(PAPEIS_COLUNA);
  for (const [chave, valor] of Object.entries(mapeamento.sugestoes ?? {})) {
    if (papeis.has(chave) && mapeamento.colunas.includes(valor)) {
      iniciais[valor] = chave as PapelColuna;
    } else if (mapeamento.colunas.includes(chave) && papeis.has(valor)) {
      iniciais[chave] = valor as PapelColuna;
    }
  }
  return iniciais;
}

export function MapeadorColunas({ mapeamento, aplicando, onAplicar }: MapeadorColunasProps) {
  const [atribuicoes, setAtribuicoes] = useState<Record<string, PapelColuna>>(() =>
    atribuicoesIniciais(mapeamento)
  );

  const duplicados = useMemo(() => {
    const contagem = new Map<string, number>();
    for (const papel of Object.values(atribuicoes)) {
      if (papel === "ignorar") continue;
      contagem.set(papel, (contagem.get(papel) ?? 0) + 1);
    }
    return Array.from(contagem.entries())
      .filter(([, quantidade]) => quantidade > 1)
      .map(([papel]) => ROTULOS_PAPEL[papel as PapelColuna] ?? papel);
  }, [atribuicoes]);

  const temData = Object.values(atribuicoes).includes("data");
  const temValor =
    Object.values(atribuicoes).includes("valor") ||
    (Object.values(atribuicoes).includes("debito") &&
      Object.values(atribuicoes).includes("credito"));

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Mapeie as colunas do arquivo</CardTitle>
        <CardDescription>
          {mapeamento.mensagem ??
            "O motor não reconheceu as colunas automaticamente. Diga o que cada coluna significa e aplique o mapeamento para gerar a prévia."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <Table>
          <TableHeader>
            <TableRow>
              {mapeamento.colunas.map((coluna) => (
                <TableHead key={coluna} className="min-w-[10rem] align-top">
                  <span className="block truncate normal-case tracking-normal text-foreground">
                    {coluna}
                  </span>
                  <select
                    aria-label={`Papel da coluna ${coluna}`}
                    value={atribuicoes[coluna] ?? "ignorar"}
                    onChange={(evento) =>
                      setAtribuicoes((atuais) => ({
                        ...atuais,
                        [coluna]: evento.target.value as PapelColuna,
                      }))
                    }
                    className={cn(
                      "mt-1.5 h-8 w-full rounded-lg border border-white/10 bg-surface-raised px-2 text-xs font-normal normal-case tracking-normal shadow-sm",
                      "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                      (atribuicoes[coluna] ?? "ignorar") === "ignorar"
                        ? "text-muted-foreground"
                        : "text-foreground"
                    )}
                  >
                    {PAPEIS_COLUNA.map((papel) => (
                      <option key={papel} value={papel}>
                        {ROTULOS_PAPEL[papel]}
                      </option>
                    ))}
                  </select>
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {mapeamento.amostra.map((linha, indice) => (
              <TableRow key={indice}>
                {mapeamento.colunas.map((coluna) => (
                  <TableCell key={coluna} className="whitespace-nowrap text-xs">
                    {textoOuTraco(linha[coluna] == null ? "" : String(linha[coluna]))}
                  </TableCell>
                ))}
              </TableRow>
            ))}
          </TableBody>
        </Table>

        {duplicados.length > 0 && (
          <p className="flex items-center gap-2 text-xs text-amber-400" role="alert">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden />
            Papel repetido em mais de uma coluna: {duplicados.join(", ")}. Cada papel
            (fora “Ignorar”) só pode aparecer uma vez.
          </p>
        )}
        {(!temData || !temValor) && (
          <p className="text-xs text-amber-400">
            Para gerar a prévia, mapeie ao menos a coluna de data e a de valor (ou o
            par débito/crédito).
          </p>
        )}

        <Button
          onClick={() => onAplicar(atribuicoes)}
          disabled={aplicando || duplicados.length > 0}
        >
          {aplicando ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Check className="h-4 w-4" />
          )}
          Aplicar mapeamento
        </Button>
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Prévia do lote
// --------------------------------------------------------------------------

function CartaoResumo({
  rotulo,
  valor,
  detalhe,
  tom,
}: {
  rotulo: string;
  valor: string;
  detalhe?: string;
  tom?: "esmeralda" | "carmim";
}) {
  return (
    <Card>
      <CardContent className="space-y-1 p-4">
        <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
          {rotulo}
        </p>
        <p
          className={cn(
            "text-lg font-semibold tabular-nums",
            tom === "esmeralda" && "text-profit",
            tom === "carmim" && "text-loss"
          )}
        >
          {valor}
        </p>
        {detalhe && <p className="text-xs text-muted-foreground">{detalhe}</p>}
      </CardContent>
    </Card>
  );
}

function descricaoDeTransacao(transacao: Partial<Transacao>): string {
  return textoOuTraco(
    transacao.descricao_normalizada ?? transacao.descricao_original ?? null
  );
}

function TabelaAmostra({ amostra }: { amostra: Array<Partial<Transacao>> }) {
  if (amostra.length === 0) return null;
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Amostra das transações normalizadas</CardTitle>
        <CardDescription>
          As {formatarInteiro(amostra.length)} primeiras linhas como o motor as
          entendeu. Nada foi gravado ainda.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Data</TableHead>
              <TableHead>Descrição</TableHead>
              <TableHead>Tipo</TableHead>
              <TableHead className="text-right">Valor</TableHead>
              <TableHead>Origem</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {amostra.map((transacao, indice) => {
              const credito = transacao.direcao === "credito";
              return (
                <TableRow key={transacao.fingerprint ?? indice}>
                  <TableCell className="whitespace-nowrap text-xs">
                    {formatarDataISO(transacao.data_operacao)}
                  </TableCell>
                  <TableCell className="max-w-[22rem]">
                    <span className="block truncate text-xs">
                      {descricaoDeTransacao(transacao)}
                    </span>
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                    {textoOuTraco(transacao.tipo ?? null)}
                  </TableCell>
                  <TableCell
                    className={cn(
                      "whitespace-nowrap text-right text-xs font-medium tabular-nums",
                      credito ? "text-profit" : "text-loss"
                    )}
                  >
                    {transacao.valor_centavos != null
                      ? `${credito ? "+" : "−"}${formatarCentavos(transacao.valor_centavos)}`
                      : "—"}
                  </TableCell>
                  <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                    {textoOuTraco(transacao.origem_ref ?? null)}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
}

interface PreviaLoteProps {
  previa: PreviaImportacao;
  ocupado: "confirmando" | "descartando" | "mapeando" | null;
  onConfirmar: () => void;
  onDescartar: () => void;
  onAplicarMapeamento: (atribuicoes: Record<string, string>) => void;
}

function PreviaLote({
  previa,
  ocupado,
  onConfirmar,
  onDescartar,
  onAplicarMapeamento,
}: PreviaLoteProps) {
  const status = STATUS_ARQUIVO[previa.status] ?? {
    rotulo: previa.status,
    variante: "muted" as VarianteBadge,
  };
  const precisaMapear = previa.mapeamento_necessario !== null;
  const semTransacoes = previa.contagens.transacoes === 0;

  const itensDuplicidades = previa.duplicidades.map((duplicidade) => (
    <>
      {duplicidade.motivo}
      {duplicidade.transacao && (
        <span className="text-muted-foreground">
          {" "}
          — {descricaoDeTransacao(duplicidade.transacao)}
          {duplicidade.transacao.valor_centavos != null &&
            ` (${formatarCentavos(duplicidade.transacao.valor_centavos)})`}
        </span>
      )}
    </>
  ));

  const itensBaixaConfianca = previa.baixa_confianca.map((transacao) => (
    <>
      {formatarDataISO(transacao.data_operacao)} · {descricaoDeTransacao(transacao)} ·{" "}
      {centavosOuTraco(transacao.valor_centavos)}
      {transacao.confianca != null && (
        <span className="text-muted-foreground">
          {" "}
          (confiança {Math.round(transacao.confianca * 100)}%)
        </span>
      )}
    </>
  ));

  return (
    <section aria-label="Prévia da importação" className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold tracking-tight">Prévia da importação</h2>
        <Badge variant={status.variante}>{status.rotulo}</Badge>
        <Badge variant="muted">lote #{previa.lote_id}</Badge>
        {previa.adaptador && <Badge variant="outline">{previa.adaptador}</Badge>}
      </div>

      {precisaMapear && previa.mapeamento_necessario ? (
        <MapeadorColunas
          key={previa.lote_id}
          mapeamento={previa.mapeamento_necessario}
          aplicando={ocupado === "mapeando"}
          onAplicar={onAplicarMapeamento}
        />
      ) : (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <CartaoResumo
              rotulo="Período"
              valor={
                previa.periodo.inicio || previa.periodo.fim
                  ? `${formatarDataISO(previa.periodo.inicio)} – ${formatarDataISO(previa.periodo.fim)}`
                  : "—"
              }
              detalhe={`${formatarInteiro(previa.contagens.linhas)} linhas lidas`}
            />
            <CartaoResumo
              rotulo="Lançamentos"
              valor={formatarInteiro(previa.contagens.transacoes)}
              detalhe={`${formatarInteiro(previa.contagens.creditos)} créditos · ${formatarInteiro(previa.contagens.debitos)} débitos`}
            />
            <CartaoResumo
              rotulo="Entradas"
              valor={formatarCentavos(previa.totais.entradas_centavos)}
              tom="esmeralda"
            />
            <CartaoResumo
              rotulo="Saídas"
              valor={formatarCentavos(previa.totais.saidas_centavos)}
              detalhe={
                previa.totais.compras_cartao_centavos > 0
                  ? `Compras no cartão: ${formatarCentavos(previa.totais.compras_cartao_centavos)}`
                  : undefined
              }
              tom="carmim"
            />
          </div>

          {previa.saldos.length > 0 && (
            <Card>
              <CardContent className="p-4">
                <p className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                  Saldos encontrados no documento
                </p>
                <ul className="mt-2 grid grid-cols-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
                  {previa.saldos.map((saldo, indice) => (
                    <li
                      key={`${saldo.rotulo}-${saldo.data}-${indice}`}
                      className="flex items-baseline justify-between gap-2 rounded-lg bg-white/[0.03] px-3 py-1.5 text-sm"
                    >
                      <span className="min-w-0 truncate text-xs text-muted-foreground">
                        {saldo.rotulo} · {formatarDataISO(saldo.data)}
                      </span>
                      <span className="font-medium tabular-nums">
                        {formatarCentavos(saldo.valor_centavos)}
                      </span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}

          <TabelaAmostra amostra={previa.amostra} />
        </>
      )}

      <div className="space-y-2">
        <ListaExpansivel
          titulo="Divergências contábeis (o saldo não fecha)"
          tom="carmim"
          itens={previa.divergencias}
          abertaInicialmente
        />
        <ListaExpansivel titulo="Erros por linha" tom="carmim" itens={previa.erros} />
        <ListaExpansivel
          titulo="Possíveis duplicidades"
          tom="ambar"
          itens={itensDuplicidades}
        />
        <ListaExpansivel
          titulo="Lançamentos de baixa confiança"
          tom="ambar"
          itens={itensBaixaConfianca}
        />
        <ListaExpansivel titulo="Avisos" tom="ambar" itens={previa.avisos} />
        <ListaExpansivel
          titulo="Campos ausentes no documento"
          tom="ambar"
          itens={previa.campos_ausentes}
        />
      </div>

      <div className="flex flex-wrap gap-2">
        <Button
          onClick={onConfirmar}
          disabled={ocupado !== null || precisaMapear || semTransacoes}
          title={
            precisaMapear
              ? "Aplique o mapeamento de colunas antes de confirmar."
              : semTransacoes
                ? "Nenhuma transação reconhecida para importar."
                : undefined
          }
        >
          {ocupado === "confirmando" ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Check className="h-4 w-4" />
          )}
          Confirmar importação
        </Button>
        <Button variant="outline" onClick={onDescartar} disabled={ocupado !== null}>
          {ocupado === "descartando" ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Trash2 className="h-4 w-4" />
          )}
          Descartar
        </Button>
        <p className="w-full text-xs text-muted-foreground">
          Nada foi gravado nas transações ainda — a confirmação é atômica e pode ser
          revertida depois na lista de arquivos.
        </p>
      </div>
    </section>
  );
}

// --------------------------------------------------------------------------
// Lista de arquivos/lotes
// --------------------------------------------------------------------------

interface ListaArquivosProps {
  arquivos: ArquivoComLote[];
  carregando: boolean;
  nomesInstituicoes: Map<number, string>;
  nomesContas: Map<number, string>;
  nomesCartoes: Map<number, string>;
  ocupadoArquivoId: number | null;
  onRecarregar: () => void;
  onReprocessar: (arquivo: ArquivoComLote) => void;
  onReverter: (arquivo: ArquivoComLote) => void;
}

function ListaArquivos({
  arquivos,
  carregando,
  nomesInstituicoes,
  nomesContas,
  nomesCartoes,
  ocupadoArquivoId,
  onRecarregar,
  onReprocessar,
  onReverter,
}: ListaArquivosProps) {
  return (
    <Card>
      <CardHeader className="flex-row items-start justify-between space-y-0">
        <div className="space-y-1.5">
          <CardTitle className="text-base">Arquivos importados</CardTitle>
          <CardDescription>
            Cada arquivo guarda o original imutável e o histórico de lotes. Reprocessar
            cria um lote novo; reverter desfaz as transações do lote confirmado.
          </CardDescription>
        </div>
        <Button variant="ghost" size="sm" onClick={onRecarregar} disabled={carregando}>
          <RefreshCw className={cn("h-4 w-4", carregando && "animate-spin")} />
          Atualizar
        </Button>
      </CardHeader>
      <CardContent>
        {carregando && arquivos.length === 0 ? (
          <div className="space-y-2">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-2/3" />
          </div>
        ) : arquivos.length === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">
            Nenhum arquivo importado ainda. Envie o primeiro extrato ou fatura acima.
          </p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Arquivo</TableHead>
                <TableHead>Instituição / conta</TableHead>
                <TableHead>Tipo</TableHead>
                <TableHead>Período</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Ações</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {arquivos.map((arquivo) => {
                const status = STATUS_ARQUIVO[arquivo.status] ?? {
                  rotulo: arquivo.status,
                  variante: "muted" as VarianteBadge,
                };
                const nomeInstituicao =
                  arquivo.instituicao_id != null
                    ? nomesInstituicoes.get(arquivo.instituicao_id)
                    : undefined;
                const nomeContaOuCartao =
                  arquivo.conta_id != null
                    ? nomesContas.get(arquivo.conta_id)
                    : arquivo.cartao_id != null
                      ? nomesCartoes.get(arquivo.cartao_id)
                      : undefined;
                const loteId = loteDoArquivo(arquivo);
                const ocupado = ocupadoArquivoId === arquivo.id;
                const podeReverter =
                  loteId !== null &&
                  arquivo.status !== "revertido" &&
                  arquivo.status !== "falhou";
                return (
                  <TableRow key={arquivo.id}>
                    <TableCell className="max-w-[16rem]">
                      <span className="flex items-center gap-2">
                        <FileText
                          className="h-4 w-4 shrink-0 text-muted-foreground"
                          aria-hidden
                        />
                        <span className="min-w-0">
                          <span className="block truncate text-sm">
                            {arquivo.nome_original}
                          </span>
                          <span className="block text-[11px] text-muted-foreground">
                            {formatarDataHoraISO(arquivo.enviado_em)} ·{" "}
                            {formatarTamanho(arquivo.tamanho)}
                          </span>
                        </span>
                      </span>
                    </TableCell>
                    <TableCell className="text-xs">
                      <span className="block">{textoOuTraco(nomeInstituicao ?? null)}</span>
                      <span className="block text-muted-foreground">
                        {textoOuTraco(nomeContaOuCartao ?? null)}
                      </span>
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                      {ROTULOS_TIPO_DOCUMENTO[arquivo.tipo_documento] ??
                        arquivo.tipo_documento}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-xs">
                      {arquivo.periodo_inicio || arquivo.periodo_fim
                        ? `${formatarDataISO(arquivo.periodo_inicio)} – ${formatarDataISO(arquivo.periodo_fim)}`
                        : "—"}
                    </TableCell>
                    <TableCell>
                      <Badge variant={status.variante}>{status.rotulo}</Badge>
                      {arquivo.status === "falhou" && arquivo.erro && (
                        <span className="mt-1 block max-w-[14rem] truncate text-[11px] text-loss">
                          {arquivo.erro}
                        </span>
                      )}
                    </TableCell>
                    <TableCell>
                      <span className="flex justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          title="Reprocessar (cria um lote novo)"
                          aria-label={`Reprocessar ${arquivo.nome_original}`}
                          disabled={ocupado}
                          onClick={() => onReprocessar(arquivo)}
                        >
                          {ocupado ? (
                            <Loader2 className="h-4 w-4 animate-spin" />
                          ) : (
                            <RefreshCw className="h-4 w-4" />
                          )}
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          title={
                            podeReverter
                              ? "Reverter o último lote deste arquivo"
                              : "Sem lote para reverter"
                          }
                          aria-label={`Reverter ${arquivo.nome_original}`}
                          disabled={ocupado || !podeReverter}
                          onClick={() => onReverter(arquivo)}
                        >
                          <Undo2 className="h-4 w-4" />
                        </Button>
                        <Button
                          variant="ghost"
                          size="icon"
                          asChild
                          title="Baixar o arquivo original"
                        >
                          <a
                            href={urlDownloadOriginal(arquivo.id)}
                            download={arquivo.nome_original}
                            aria-label={`Baixar original de ${arquivo.nome_original}`}
                          >
                            <Download className="h-4 w-4" />
                          </a>
                        </Button>
                      </span>
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

// --------------------------------------------------------------------------
// Componente público da aba
// --------------------------------------------------------------------------

type EstadoAba = "ocioso" | "enviando" | "previa";

export function AbaImportar() {
  const idBase = useId();

  // Formulário pré-upload (obrigatório antes da dropzone liberar).
  const [tipoDocumento, setTipoDocumento] = useState<TipoDocumentoUpload>("extrato_conta");
  const [instituicao, setInstituicao] = useState("");
  const [conta, setConta] = useState("");
  const [cartao, setCartao] = useState("");
  const [periodo, setPeriodo] = useState("");
  const [senhaPdf, setSenhaPdf] = useState("");

  // Cadastros para os comboboxes e para nomear a lista de arquivos.
  const [instituicoes, setInstituicoes] = useState<OpcaoCombobox[]>([]);
  const [contas, setContas] = useState<OpcaoCombobox[]>([]);
  const [cartoes, setCartoes] = useState<OpcaoCombobox[]>([]);
  const [nomesInstituicoes, setNomesInstituicoes] = useState<Map<number, string>>(new Map());
  const [nomesContas, setNomesContas] = useState<Map<number, string>>(new Map());
  const [nomesCartoes, setNomesCartoes] = useState<Map<number, string>>(new Map());

  // Ciclo de vida do envio/prévia.
  const [estado, setEstado] = useState<EstadoAba>("ocioso");
  const [previa, setPrevia] = useState<PreviaImportacao | null>(null);
  const [ocupado, setOcupado] = useState<"confirmando" | "descartando" | "mapeando" | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [sucesso, setSucesso] = useState<string | null>(null);
  const [nomeArquivo, setNomeArquivo] = useState<string | null>(null);

  // Lista de arquivos/lotes.
  const [arquivos, setArquivos] = useState<ArquivoComLote[]>([]);
  const [carregandoArquivos, setCarregandoArquivos] = useState(true);
  const [ocupadoArquivoId, setOcupadoArquivoId] = useState<number | null>(null);

  const montado = useRef(true);
  useEffect(() => {
    montado.current = true;
    return () => {
      montado.current = false;
    };
  }, []);

  const recarregarCadastros = useCallback(() => {
    void Promise.allSettled([listarInstituicoes(), listarContas(), listarCartoes()]).then(
      ([resInstituicoes, resContas, resCartoes]) => {
        if (!montado.current) return;
        if (resInstituicoes.status === "fulfilled" && Array.isArray(resInstituicoes.value)) {
          setInstituicoes(resInstituicoes.value.map((item) => ({ nome: item.nome })));
          setNomesInstituicoes(new Map(resInstituicoes.value.map((item) => [item.id, item.nome])));
        }
        if (resContas.status === "fulfilled" && Array.isArray(resContas.value)) {
          setContas(
            resContas.value.map((item) => ({
              nome: item.nome,
              detalhe: item.instituicao_nome ?? null,
            }))
          );
          setNomesContas(new Map(resContas.value.map((item) => [item.id, item.nome])));
        }
        if (resCartoes.status === "fulfilled" && Array.isArray(resCartoes.value)) {
          setCartoes(
            resCartoes.value.map((item) => ({
              nome: item.nome,
              detalhe: item.final ? `final ${item.final}` : (item.emissor ?? null),
            }))
          );
          setNomesCartoes(new Map(resCartoes.value.map((item) => [item.id, item.nome])));
        }
      }
    );
  }, []);

  const recarregarArquivos = useCallback(() => {
    setCarregandoArquivos(true);
    listarArquivos()
      .then((lista) => {
        if (!montado.current) return;
        setArquivos(lista as ArquivoComLote[]);
      })
      .catch(() => {
        // A página cuida do aviso de backend fora do ar; aqui só paramos o spinner.
      })
      .finally(() => {
        if (montado.current) setCarregandoArquivos(false);
      });
  }, []);

  useEffect(() => {
    recarregarCadastros();
    recarregarArquivos();
  }, [recarregarCadastros, recarregarArquivos]);

  const formularioValido =
    instituicao.trim() !== "" &&
    (tipoDocumento === "extrato_conta" ? conta.trim() !== "" : cartao.trim() !== "");

  const motivoBloqueio =
    instituicao.trim() === ""
      ? "Preencha a instituição antes de enviar o arquivo."
      : tipoDocumento === "extrato_conta"
        ? "Preencha a conta antes de enviar o arquivo."
        : "Preencha o cartão antes de enviar o arquivo.";

  const enviar = useCallback(
    async (arquivo: File) => {
      setErro(null);
      setSucesso(null);
      setPrevia(null);
      setNomeArquivo(arquivo.name);
      setEstado("enviando");
      try {
        const formulario = new FormData();
        formulario.append("file", arquivo, arquivo.name);
        formulario.append("tipo_documento", tipoDocumento);
        formulario.append("instituicao", instituicao.trim());
        if (tipoDocumento === "extrato_conta") {
          formulario.append("conta", conta.trim());
        } else {
          formulario.append("cartao", cartao.trim());
        }
        if (periodo.trim() !== "") formulario.append("periodo", periodo.trim());
        if (senhaPdf !== "") formulario.append("senha_pdf", senhaPdf);

        const resposta = await enviarArquivo(formulario);
        if (!montado.current) return;
        setPrevia(resposta);
        setEstado("previa");
        recarregarArquivos();
        recarregarCadastros();
      } catch (falha) {
        if (!montado.current) return;
        setErro(
          mensagemDeFalha(
            falha,
            "Não foi possível enviar o arquivo. Verifique o motor e tente de novo."
          )
        );
        setEstado("ocioso");
      }
    },
    [cartao, conta, instituicao, periodo, recarregarArquivos, recarregarCadastros, senhaPdf, tipoDocumento]
  );

  const confirmar = useCallback(async () => {
    if (!previa) return;
    setOcupado("confirmando");
    setErro(null);
    try {
      await confirmarLote(previa.lote_id);
      if (!montado.current) return;
      setSucesso(
        `Importação confirmada: ${formatarInteiro(previa.contagens.transacoes)} lançamentos gravados do lote #${previa.lote_id}.`
      );
      setPrevia(null);
      setEstado("ocioso");
      setNomeArquivo(null);
      recarregarArquivos();
    } catch (falha) {
      if (!montado.current) return;
      setErro(mensagemDeFalha(falha, "Não foi possível confirmar o lote."));
    } finally {
      if (montado.current) setOcupado(null);
    }
  }, [previa, recarregarArquivos]);

  const descartar = useCallback(async () => {
    if (!previa) return;
    setOcupado("descartando");
    setErro(null);
    try {
      await reverterLote(previa.lote_id, "Descartado na prévia, antes da confirmação.");
    } catch {
      // Lote nem chegou a existir do lado de lá: descartar localmente basta.
    }
    if (!montado.current) return;
    setOcupado(null);
    setPrevia(null);
    setEstado("ocioso");
    setNomeArquivo(null);
    setSucesso("Prévia descartada. Nada foi gravado.");
    recarregarArquivos();
  }, [previa, recarregarArquivos]);

  const aplicarMapa = useCallback(
    async (atribuicoes: Record<string, string>) => {
      if (!previa) return;
      setOcupado("mapeando");
      setErro(null);
      try {
        const resposta = await aplicarMapeamento(previa.lote_id, atribuicoes);
        if (!montado.current) return;
        setPrevia(resposta);
      } catch (falha) {
        if (!montado.current) return;
        setErro(mensagemDeFalha(falha, "Não foi possível aplicar o mapeamento."));
      } finally {
        if (montado.current) setOcupado(null);
      }
    },
    [previa]
  );

  const reprocessar = useCallback(
    async (arquivo: ArquivoComLote) => {
      setOcupadoArquivoId(arquivo.id);
      setErro(null);
      setSucesso(null);
      try {
        const resposta = await reprocessarArquivo(
          arquivo.id,
          senhaPdf !== "" ? senhaPdf : undefined
        );
        if (!montado.current) return;
        setPrevia(resposta);
        setNomeArquivo(arquivo.nome_original);
        setEstado("previa");
        recarregarArquivos();
      } catch (falha) {
        if (!montado.current) return;
        setErro(mensagemDeFalha(falha, "Não foi possível reprocessar o arquivo."));
      } finally {
        if (montado.current) setOcupadoArquivoId(null);
      }
    },
    [recarregarArquivos, senhaPdf]
  );

  const reverter = useCallback(
    async (arquivo: ArquivoComLote) => {
      const loteId = loteDoArquivo(arquivo);
      if (loteId === null) return;
      setOcupadoArquivoId(arquivo.id);
      setErro(null);
      setSucesso(null);
      try {
        await reverterLote(loteId, `Revertido pela lista de arquivos (${arquivo.nome_original}).`);
        if (!montado.current) return;
        setSucesso(`Lote #${loteId} de “${arquivo.nome_original}” revertido.`);
        recarregarArquivos();
      } catch (falha) {
        if (!montado.current) return;
        setErro(mensagemDeFalha(falha, "Não foi possível reverter o lote."));
      } finally {
        if (montado.current) setOcupadoArquivoId(null);
      }
    },
    [recarregarArquivos]
  );

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
        <>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Antes de enviar</CardTitle>
              <CardDescription>
                Diga de onde vem o documento — o motor usa isso para criar a conta ou o
                cartão, deduplicar e conciliar. Campos com * são obrigatórios.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <fieldset>
                <legend className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
                  Tipo de documento
                  <span className="text-loss" aria-hidden>
                    {" "}
                    *
                  </span>
                </legend>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  {(
                    [
                      {
                        valor: "extrato_conta" as const,
                        rotulo: "Extrato de conta",
                        detalhe: "Conta corrente, poupança ou pagamento",
                        Icone: Landmark,
                      },
                      {
                        valor: "fatura_cartao" as const,
                        rotulo: "Fatura de cartão",
                        detalhe: "Compras, estornos e pagamentos do cartão",
                        Icone: CreditCard,
                      },
                    ] as const
                  ).map(({ valor, rotulo, detalhe, Icone }) => (
                    <div key={valor}>
                      <input
                        type="radio"
                        id={`${idBase}-tipo-${valor}`}
                        name={`${idBase}-tipo-documento`}
                        value={valor}
                        checked={tipoDocumento === valor}
                        onChange={() => setTipoDocumento(valor)}
                        className="peer sr-only"
                      />
                      <label
                        htmlFor={`${idBase}-tipo-${valor}`}
                        className={cn(
                          "flex cursor-pointer items-center gap-3 rounded-xl border px-4 py-3 transition-colors",
                          "peer-focus-visible:ring-2 peer-focus-visible:ring-ring peer-focus-visible:ring-offset-2 peer-focus-visible:ring-offset-background",
                          tipoDocumento === valor
                            ? "border-primary/50 bg-primary/10"
                            : "border-white/10 bg-surface hover:border-white/25"
                        )}
                      >
                        <Icone
                          className={cn(
                            "h-5 w-5 shrink-0",
                            tipoDocumento === valor ? "text-primary" : "text-muted-foreground"
                          )}
                          aria-hidden
                        />
                        <span className="min-w-0">
                          <span className="block text-sm font-medium">{rotulo}</span>
                          <span className="block text-xs text-muted-foreground">{detalhe}</span>
                        </span>
                      </label>
                    </div>
                  ))}
                </div>
              </fieldset>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <ComboboxCriavel
                  id={`${idBase}-instituicao`}
                  rotulo="Instituição"
                  substantivo="instituição"
                  feminino
                  obrigatorio
                  valor={instituicao}
                  onChange={setInstituicao}
                  opcoes={instituicoes}
                  placeholder="Banco, corretora ou emissor"
                />
                {tipoDocumento === "extrato_conta" ? (
                  <ComboboxCriavel
                    id={`${idBase}-conta`}
                    rotulo="Conta"
                    substantivo="conta"
                    feminino
                    obrigatorio
                    valor={conta}
                    onChange={setConta}
                    opcoes={contas}
                    placeholder="Ex.: Conta corrente principal"
                  />
                ) : (
                  <ComboboxCriavel
                    id={`${idBase}-cartao`}
                    rotulo="Cartão"
                    substantivo="cartão"
                    obrigatorio
                    valor={cartao}
                    onChange={setCartao}
                    opcoes={cartoes}
                    placeholder="Ex.: Itau The One"
                  />
                )}
              </div>

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="flex min-w-0 flex-col gap-1">
                  <label
                    htmlFor={`${idBase}-periodo`}
                    className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground"
                  >
                    Período (opcional)
                  </label>
                  <Input
                    id={`${idBase}-periodo`}
                    value={periodo}
                    onChange={(evento) => setPeriodo(evento.target.value)}
                    placeholder="Ex.: 2026-07 ou 2026-07-01 a 2026-07-31"
                  />
                </div>
                <div className="flex min-w-0 flex-col gap-1">
                  <label
                    htmlFor={`${idBase}-senha`}
                    className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground"
                  >
                    Senha do PDF (opcional)
                  </label>
                  <Input
                    id={`${idBase}-senha`}
                    type="password"
                    autoComplete="off"
                    value={senhaPdf}
                    onChange={(evento) => setSenhaPdf(evento.target.value)}
                    placeholder="Só para PDFs protegidos"
                    aria-describedby={`${idBase}-senha-aviso`}
                  />
                  <p id={`${idBase}-senha-aviso`} className="text-[11px] text-muted-foreground">
                    Usada só nesta requisição, nunca armazenada.
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>

          {estado === "enviando" ? (
            <EstadoProcessando nomeArquivo={nomeArquivo} />
          ) : (
            <DropzoneArquivo
              id={`${idBase}-arquivo`}
              extensoes={EXTENSOES_EXTRATO}
              onArquivo={(arquivo) => void enviar(arquivo)}
              onErro={(mensagem) => {
                setErro(mensagem);
                setSucesso(null);
              }}
              desabilitado={!formularioValido}
              motivoBloqueio={motivoBloqueio}
              titulo="Arraste o extrato ou a fatura aqui, ou clique para escolher"
              descricao=".csv, .xlsx, .xls, .ofx, .qfx, .xml ou .pdf (até 15 MB) — o original fica guardado imutável"
            />
          )}
        </>
      )}

      {estado === "previa" && previa && (
        <PreviaLote
          previa={previa}
          ocupado={ocupado}
          onConfirmar={() => void confirmar()}
          onDescartar={() => void descartar()}
          onAplicarMapeamento={(atribuicoes) => void aplicarMapa(atribuicoes)}
        />
      )}

      <ListaArquivos
        arquivos={arquivos}
        carregando={carregandoArquivos}
        nomesInstituicoes={nomesInstituicoes}
        nomesContas={nomesContas}
        nomesCartoes={nomesCartoes}
        ocupadoArquivoId={ocupadoArquivoId}
        onRecarregar={recarregarArquivos}
        onReprocessar={(arquivo) => void reprocessar(arquivo)}
        onReverter={(arquivo) => void reverter(arquivo)}
      />
    </div>
  );
}

export default AbaImportar;
