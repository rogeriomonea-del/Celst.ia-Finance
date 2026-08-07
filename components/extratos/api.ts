/**
 * Cliente tipado da API de extratos (`/api/py/extratos/*`).
 *
 * Em dev o rewrite de `next.config.mjs` manda para `python3 -m engine.server`;
 * na Vercel cai na função `api/extratos.py`. Todas as respostas de erro vêm
 * como `{"erro": "mensagem em pt-BR"}` — aqui isso vira `ErroApiExtratos`,
 * com `status` e uma mensagem sempre apresentável ao usuário.
 */

import type {
  ArquivoImportado,
  AtualizacaoTransacao,
  Cartao,
  CategoriaNo,
  ColunaOrdenacao,
  Conta,
  DirecaoOrdenacao,
  Duplicidade,
  ErroApi,
  Fatura,
  FiltrosExtratos,
  FluxoResultado,
  Instituicao,
  ItemConciliacao,
  PaginaTransacoes,
  Painel,
  ParteDivisao,
  PreviaImportacao,
  RegraCategoria,
  Transacao,
  VisaoFluxo,
} from "@/lib/types-extratos";

/** Base de todas as chamadas (reescrita para o motor Python). */
export const BASE_EXTRATOS = "/api/py/extratos";

const MENSAGENS_POR_STATUS: Record<number, string> = {
  400: "A requisição não foi aceita pelo motor. Confira os dados e tente de novo.",
  404: "O serviço de extratos não respondeu neste endereço. Se você está rodando localmente, suba o motor com python3 -m engine.server.",
  409: "Conflito: este arquivo já foi importado antes. Use reprocessar se quiser um novo lote.",
  413: "Arquivo grande demais para o limite configurado no servidor.",
  429: "Muitas requisições seguidas. Espere alguns segundos e tente de novo.",
  500: "O motor encontrou um erro inesperado.",
  503: "O armazenamento durável não está disponível neste ambiente. Rode localmente com python3 -m engine.server ou configure CELESTIA_DATA_DURAVEL=1.",
};

const MENSAGEM_PADRAO =
  "Não foi possível falar com o serviço de extratos. Verifique se o motor está no ar (python3 -m engine.server) e tente de novo.";

/** Erro de API sempre com mensagem pt-BR pronta para exibição. */
export class ErroApiExtratos extends Error {
  /** HTTP status; 0 quando a rede falhou antes de qualquer resposta. */
  readonly status: number;

  constructor(mensagem: string, status: number) {
    super(mensagem);
    this.name = "ErroApiExtratos";
    this.status = status;
  }

  /** true quando o backend nem respondeu (motor fora do ar / sem rede). */
  get backendIndisponivel(): boolean {
    return this.status === 0 || this.status === 404 || this.status === 502;
  }
}

/** O corpo tem cara de `{erro: "..."}`? */
function ehErroApi(corpo: unknown): corpo is ErroApi {
  return (
    typeof corpo === "object" &&
    corpo !== null &&
    typeof (corpo as Record<string, unknown>).erro === "string" &&
    ((corpo as Record<string, unknown>).erro as string).trim() !== ""
  );
}

function mensagemDeErro(corpo: unknown, status: number): string {
  if (ehErroApi(corpo)) return corpo.erro;
  return MENSAGENS_POR_STATUS[status] ?? MENSAGEM_PADRAO;
}

// --------------------------------------------------------------------------
// Querystring de filtros
// --------------------------------------------------------------------------

/** Parâmetros extras de listagem (paginação/ordenação), além dos filtros. */
export interface ParametrosListagem {
  pagina?: number;
  por_pagina?: number;
  ordenar?: ColunaOrdenacao;
  dir?: DirecaoOrdenacao;
}

/**
 * Converte filtros (+ extras) em `URLSearchParams`, pulando `null`/`undefined`
 * e strings vazias — a querystring só carrega o que está ativo.
 */
export function montarQuery(
  filtros?: FiltrosExtratos | null,
  extras?: Record<string, string | number | null | undefined>
): URLSearchParams {
  const params = new URLSearchParams();
  const tudo: Record<string, unknown> = { ...(filtros ?? {}), ...(extras ?? {}) };
  for (const [chave, valor] of Object.entries(tudo)) {
    if (valor === null || valor === undefined) continue;
    const texto = String(valor).trim();
    if (texto === "") continue;
    params.set(chave, texto);
  }
  return params;
}

function urlCom(caminho: string, params?: URLSearchParams): string {
  const query = params?.toString() ?? "";
  return query ? `${BASE_EXTRATOS}${caminho}?${query}` : `${BASE_EXTRATOS}${caminho}`;
}

// --------------------------------------------------------------------------
// Núcleo de fetch
// --------------------------------------------------------------------------

async function requisitar<T>(
  metodo: "GET" | "POST" | "PATCH" | "DELETE",
  caminho: string,
  opcoes?: {
    params?: URLSearchParams;
    corpo?: unknown;
    formulario?: FormData;
    signal?: AbortSignal;
  }
): Promise<T> {
  let resposta: Response;
  try {
    resposta = await fetch(urlCom(caminho, opcoes?.params), {
      method: metodo,
      signal: opcoes?.signal,
      ...(opcoes?.formulario
        ? { body: opcoes.formulario }
        : opcoes?.corpo !== undefined
          ? {
              body: JSON.stringify(opcoes.corpo),
              headers: { "Content-Type": "application/json" },
            }
          : {}),
    });
  } catch (falha) {
    // Abort não é erro de backend: repropaga para quem cancelou.
    if (falha instanceof DOMException && falha.name === "AbortError") throw falha;
    throw new ErroApiExtratos(MENSAGEM_PADRAO, 0);
  }

  const corpo: unknown = await resposta.json().catch(() => null);
  if (!resposta.ok) {
    throw new ErroApiExtratos(mensagemDeErro(corpo, resposta.status), resposta.status);
  }
  return corpo as T;
}

// --------------------------------------------------------------------------
// Endpoints tipados
// --------------------------------------------------------------------------

export function obterPainel(
  filtros?: FiltrosExtratos | null,
  signal?: AbortSignal
): Promise<Painel> {
  return requisitar<Painel>("GET", "/painel", {
    params: montarQuery(filtros),
    signal,
  });
}

export function listarTransacoes(
  filtros?: FiltrosExtratos | null,
  listagem?: ParametrosListagem,
  signal?: AbortSignal
): Promise<PaginaTransacoes> {
  return requisitar<PaginaTransacoes>("GET", "/transacoes", {
    params: montarQuery(filtros, {
      pagina: listagem?.pagina,
      por_pagina: listagem?.por_pagina,
      ordenar: listagem?.ordenar,
      dir: listagem?.dir,
    }),
    signal,
  });
}

export function atualizarTransacao(
  id: number,
  corpo: AtualizacaoTransacao
): Promise<Transacao> {
  return requisitar<Transacao>("PATCH", `/transacoes/${id}`, { corpo });
}

export function dividirTransacao(
  id: number,
  partes: ParteDivisao[],
  justificativa?: string
): Promise<{ transacao_id: number; partes: ParteDivisao[] }> {
  return requisitar("POST", `/transacoes/${id}/dividir`, {
    corpo: { partes, justificativa },
  });
}

export function listarCategorias(signal?: AbortSignal): Promise<CategoriaNo[]> {
  return requisitar<CategoriaNo[]>("GET", "/categorias", { signal });
}

export function listarCartoes(signal?: AbortSignal): Promise<Cartao[]> {
  return requisitar<Cartao[]>("GET", "/cartoes", { signal });
}

export function listarContas(signal?: AbortSignal): Promise<Conta[]> {
  return requisitar<Conta[]>("GET", "/contas", { signal });
}

export function listarInstituicoes(signal?: AbortSignal): Promise<Instituicao[]> {
  return requisitar<Instituicao[]>("GET", "/instituicoes", { signal });
}

export function listarFaturas(
  filtros?: FiltrosExtratos | null,
  signal?: AbortSignal
): Promise<Fatura[]> {
  return requisitar<Fatura[]>("GET", "/faturas", {
    params: montarQuery(filtros),
    signal,
  });
}

export function listarArquivos(signal?: AbortSignal): Promise<ArquivoImportado[]> {
  return requisitar<ArquivoImportado[]>("GET", "/arquivos", { signal });
}

export function listarRegras(signal?: AbortSignal): Promise<RegraCategoria[]> {
  return requisitar<RegraCategoria[]>("GET", "/regras", { signal });
}

export function listarDuplicidades(signal?: AbortSignal): Promise<Duplicidade[]> {
  return requisitar<Duplicidade[]>("GET", "/duplicidades", { signal });
}

export function obterFluxo(
  visao: VisaoFluxo,
  filtros?: FiltrosExtratos | null,
  signal?: AbortSignal
): Promise<FluxoResultado> {
  return requisitar<FluxoResultado>("GET", "/fluxo", {
    params: montarQuery(filtros, { visao }),
    signal,
  });
}

export function enviarArquivo(
  formulario: FormData,
  signal?: AbortSignal
): Promise<PreviaImportacao> {
  return requisitar<PreviaImportacao>("POST", "/arquivos", { formulario, signal });
}

export function confirmarLote(loteId: number): Promise<Record<string, unknown>> {
  return requisitar("POST", `/lotes/${loteId}/confirmar`, { corpo: {} });
}

export function reverterLote(
  loteId: number,
  justificativa = ""
): Promise<Record<string, unknown>> {
  return requisitar("POST", `/lotes/${loteId}/reverter`, {
    corpo: { justificativa },
  });
}

export function aplicarMapeamento(
  loteId: number,
  mapeamento: Record<string, string>
): Promise<PreviaImportacao> {
  return requisitar<PreviaImportacao>("POST", `/lotes/${loteId}/mapeamento`, {
    corpo: { mapeamento },
  });
}

export function reprocessarArquivo(
  arquivoId: number,
  senhaPdf?: string
): Promise<PreviaImportacao> {
  return requisitar<PreviaImportacao>("POST", `/arquivos/${arquivoId}/reprocessar`, {
    corpo: senhaPdf ? { senha_pdf: senhaPdf } : {},
  });
}

export function criarConciliacao(
  itens: ItemConciliacao[],
  observacao = ""
): Promise<Record<string, unknown>> {
  return requisitar("POST", "/conciliacoes", { corpo: { itens, observacao } });
}

/** URL de download do original (endpoint com token opcional no servidor). */
export function urlDownloadOriginal(arquivoId: number): string {
  return `${BASE_EXTRATOS}/arquivos/${arquivoId}/original`;
}

/**
 * Verificação leve de saúde: o motor está no ar? Devolve `"ok"`, ou
 * `"sem_persistencia"` (503 na Vercel sem storage durável), ou
 * `"indisponivel"` quando nada respondeu.
 */
export async function verificarBackend(
  signal?: AbortSignal
): Promise<"ok" | "sem_persistencia" | "indisponivel"> {
  try {
    await requisitar<Painel>("GET", "/painel", { signal });
    return "ok";
  } catch (falha) {
    if (falha instanceof ErroApiExtratos) {
      if (falha.status === 503) return "sem_persistencia";
      if (falha.backendIndisponivel) return "indisponivel";
      return "ok"; // respondeu com erro de aplicação: o motor está no ar
    }
    throw falha; // AbortError e afins seguem para quem cancelou
  }
}
