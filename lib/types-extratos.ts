/**
 * Espelho TypeScript da API de extratos (`/api/py/extratos/*`).
 *
 * O motor Python (`engine/extratos/`) serializa tudo em **snake_case**, datas
 * como texto ISO (`"2026-08-07"`) e dinheiro SEMPRE como **centavos inteiros**
 * (`*_centavos: number`). A UI só divide por 100 na hora de exibir — nunca
 * antes (ver `components/extratos/formatar-centavos.ts`).
 *
 * Campos `Optional[...]`/colunas anuláveis do SQLite viram `| null` aqui.
 */

// --------------------------------------------------------------------------
// Vocabulários (mesmos CHECKs/valores do schema em engine/extratos/store.py)
// --------------------------------------------------------------------------

export type DirecaoTransacao = "credito" | "debito";

export type TipoTransacao =
  | "receita"
  | "despesa"
  | "transferencia"
  | "compra"
  | "estorno"
  | "tarifa"
  | "juros"
  | "pagamento_fatura"
  | "ajuste";

export type StatusConciliacao =
  | "pendente"
  | "conciliada"
  | "divergente"
  | "ignorada";

export type StatusCategoria = "categorizada" | "nao_categorizada";

export type StatusFatura = "aberta" | "fechada" | "vencida" | "paga" | "parcial";

export type TipoDocumento = "extrato_conta" | "fatura_cartao" | "planilha_config";

export type StatusArquivo =
  | "recebido"
  | "validando"
  | "processando"
  | "aguardando_revisao"
  | "pronto"
  | "confirmado"
  | "falhou"
  | "revertido";

export type TipoConta = "corrente" | "poupanca" | "pagamento" | "investimento";

export type TipoInstituicao = "banco" | "corretora" | "emissor_cartao" | "outro";

export type VisaoFluxo = "competencia" | "realizado" | "projetado";

// --------------------------------------------------------------------------
// Filtros combináveis (espelho de engine/extratos/consultas.Filtros)
// --------------------------------------------------------------------------

/**
 * Filtros da tabela/painel — todos opcionais e combináveis. `undefined`/`null`
 * significa "sem filtro"; a querystring só carrega os preenchidos.
 */
export interface FiltrosExtratos {
  periodo_inicio?: string | null; // ISO AAAA-MM-DD (inclusive)
  periodo_fim?: string | null; // ISO AAAA-MM-DD (inclusive)
  busca?: string | null; // descrição/contraparte, case/acento-insensível
  instituicao_id?: number | null;
  conta_id?: number | null;
  cartao_id?: number | null;
  fatura_id?: number | null;
  categoria_id?: number | null;
  tipo?: TipoTransacao | null;
  direcao?: DirecaoTransacao | null;
  valor_min_centavos?: number | null;
  valor_max_centavos?: number | null;
  moeda?: string | null; // ISO 4217, ex.: "BRL"
  status_conciliacao?: StatusConciliacao | null;
  status_categoria?: StatusCategoria | null;
  lote_id?: number | null;
  arquivo_id?: number | null;
}

/** Colunas aceitas em `?ordenar=` (lista branca do motor). */
export type ColunaOrdenacao =
  | "data_operacao"
  | "valor_centavos"
  | "descricao_normalizada"
  | "id";

export type DirecaoOrdenacao = "asc" | "desc";

// --------------------------------------------------------------------------
// Cadastro: instituições, contas, cartões, faturas
// --------------------------------------------------------------------------

export interface Instituicao {
  id: number;
  nome: string;
  tipo: TipoInstituicao | string;
}

export interface Conta {
  id: number;
  instituicao_id: number | null;
  nome: string;
  tipo: TipoConta | string;
  moeda: string;
  apelido: string;
  criada_em: string;
  /** Nome da instituição, quando a API faz o join. */
  instituicao_nome?: string | null;
}

export interface Cartao {
  id: number;
  instituicao_id: number | null;
  nome: string;
  emissor: string | null;
  bandeira: string | null;
  /** Últimos dígitos do cartão (nunca o número completo). */
  final: string | null;
  conta_pagadora_id: number | null;
  dia_fechamento: number | null;
  dia_vencimento: number | null;
  limite_total_centavos: number | null;
  moeda: string;
  criado_em: string;
  instituicao_nome?: string | null;
  conta_pagadora_nome?: string | null;
}

export interface Fatura {
  id: number;
  cartao_id: number;
  /** Competência AAAA-MM. */
  competencia: string;
  fecha_em: string | null;
  vence_em: string;
  /** Valor total declarado; `null` quando desconhecido (usa soma das compras). */
  valor_centavos: number | null;
  pago_centavos: number;
  status: StatusFatura | string;
  origem: string;
  cartao_nome?: string | null;
  /** Soma das compras do ciclo, quando a API a calcula. */
  compras_centavos?: number | null;
  restante_centavos?: number | null;
}

// --------------------------------------------------------------------------
// Transações
// --------------------------------------------------------------------------

/**
 * Uma linha de `GET /extratos/transacoes` — todas as colunas da tabela
 * `transacoes` mais os joins que `consultas.listar_transacoes` acrescenta
 * (`arquivo_id`/`arquivo_nome` via lote → arquivo e `categoria_nome`).
 */
export interface Transacao {
  id: number;
  lote_id: number;
  conta_id: number | null;
  cartao_id: number | null;
  fatura_id: number | null;
  data_operacao: string;
  data_lancamento: string | null;
  data_compensacao: string | null;
  /** Sempre >= 0; a direção diz se entra ou sai. */
  valor_centavos: number;
  moeda: string;
  direcao: DirecaoTransacao;
  tipo: TipoTransacao | string;
  descricao_original: string;
  descricao_normalizada: string;
  contraparte: string | null;
  id_banco: string | null;
  documento: string | null;
  saldo_apos_centavos: number | null;
  parcela_num: number | null;
  parcela_total: number | null;
  categoria_id: number | null;
  origem_categoria: string | null;
  confianca: number | null;
  status_conciliacao: StatusConciliacao | string;
  fingerprint: string;
  /** Rastro no documento: "aba!linha" | "página N" | "linha N". */
  origem_ref: string;
  arquivo_id: number | null;
  arquivo_nome: string | null;
  categoria_nome: string | null;
}

/** Resposta paginada de `GET /extratos/transacoes`. */
export interface PaginaTransacoes {
  itens: Transacao[];
  total: number;
  pagina: number;
  por_pagina: number;
}

/** Totais do conjunto filtrado (`consultas.totais` — mesma fonte da tabela). */
export interface TotaisTransacoes {
  entradas_centavos: number;
  saidas_centavos: number;
  resultado_centavos: number;
  quantidade: number;
}

/** Uma parte de `POST /extratos/transacoes/{id}/dividir` (soma == transação). */
export interface ParteDivisao {
  categoria_id: number;
  valor_centavos: number;
}

/** Corpo aceito por `PATCH /extratos/transacoes/{id}`. */
export interface AtualizacaoTransacao {
  categoria_id?: number | null;
  tipo?: TipoTransacao;
  justificativa?: string;
}

// --------------------------------------------------------------------------
// Categorias e regras
// --------------------------------------------------------------------------

/** Nó da árvore devolvida por `GET /extratos/categorias`. */
export interface CategoriaNo {
  id: number;
  nome: string;
  cor: string | null;
  /** Transações + partes de divisão apontando para a categoria. */
  uso: number;
  filhas: CategoriaNo[];
}

export type CampoRegra = "descricao" | "contraparte";
export type OperadorRegra = "contem" | "igual" | "regex" | "comeca";

export interface RegraCategoria {
  id: number;
  prioridade: number;
  ativo: number; // SQLite: 1 | 0
  campo: CampoRegra | string;
  operador: OperadorRegra | string;
  valor: string;
  conta_id: number | null;
  cartao_id: number | null;
  valor_min_centavos: number | null;
  valor_max_centavos: number | null;
  categoria_id: number;
  criada_em: string;
  categoria_nome?: string | null;
}

// --------------------------------------------------------------------------
// Arquivos, lotes e prévia de importação
// --------------------------------------------------------------------------

export interface ArquivoImportado {
  id: number;
  sha256: string;
  nome_original: string;
  mime_real: string;
  tamanho: number;
  tipo_documento: TipoDocumento | string;
  instituicao_id: number | null;
  conta_id: number | null;
  cartao_id: number | null;
  periodo_inicio: string | null;
  periodo_fim: string | null;
  enviado_em: string;
  status: StatusArquivo | string;
  versao_parser: string;
  caminho_objeto: string;
  erro: string | null;
}

export interface Lote {
  id: number;
  arquivo_id: number;
  criado_em: string;
  confirmado_em: string | null;
  revertido_em: string | null;
  resumo_json: string;
}

/** Saldo informado pelo documento, exibido na prévia. */
export interface SaldoPrevia {
  data: string;
  rotulo: string;
  valor_centavos: number;
}

/** Pedido de mapeamento manual quando o parser não reconhece as colunas. */
export interface MapeamentoNecessario {
  colunas: string[];
  /** Amostra de linhas cruas para o usuário mapear. */
  amostra: Array<Record<string, string | number | null>>;
  /** Papéis esperados (data, descricao, valor, ...) → coluna sugerida ou "". */
  sugestoes?: Record<string, string>;
  mensagem?: string;
}

export interface DuplicidadePrevia {
  transacao?: Partial<Transacao>;
  motivo: string;
  [extra: string]: unknown;
}

/** Prévia devolvida por `POST /extratos/arquivos` e pelo reprocessamento. */
export interface PreviaImportacao {
  lote_id: number;
  arquivo_id: number;
  status: StatusArquivo | string;
  adaptador: string;
  periodo: { inicio: string | null; fim: string | null };
  saldos: SaldoPrevia[];
  contagens: {
    linhas: number;
    transacoes: number;
    creditos: number;
    debitos: number;
  };
  totais: {
    entradas_centavos: number;
    saidas_centavos: number;
    compras_cartao_centavos: number;
  };
  duplicidades: DuplicidadePrevia[];
  baixa_confianca: Array<Partial<Transacao>>;
  campos_ausentes: string[];
  divergencias: string[];
  erros: string[];
  avisos: string[];
  amostra: Array<Partial<Transacao>>;
  mapeamento_necessario: MapeamentoNecessario | null;
}

/** Fila de `GET /extratos/duplicidades`. */
export interface Duplicidade {
  id: number;
  transacao_id: number;
  candidata_fingerprint: string;
  motivo: string;
  resolucao: string | null;
  resolvida_em: string | null;
  transacao?: Partial<Transacao>;
}

// --------------------------------------------------------------------------
// Fluxo (3 visões) e painel
// --------------------------------------------------------------------------

/** Um mês da série de `GET /extratos/fluxo`. */
export interface PontoFluxo {
  mes: string; // AAAA-MM
  label: string; // "ago/2026"
  entradas_centavos: number;
  saidas_centavos: number;
  resultado_centavos: number;
  saldo_acumulado_centavos: number;
  /** true quando o mês contém valores projetados (faturas não pagas). */
  projetado: boolean;
}

export interface FluxoResultado {
  visao: VisaoFluxo | string;
  granularidade: string;
  serie: PontoFluxo[];
  projecoes: Array<Record<string, string | number | null>>;
  avisos: string[];
}

/** Formatos usados pelo painel: valores em centavos ou contagens. */
export type FormatoKpiPainel = "centavos" | "numero";

/** Um cartão de KPI de `GET /extratos/painel` (engine/extratos/painel.py). */
export interface KpiPainel {
  id: string;
  label: string;
  /** Presente quando `formato === "centavos"`. */
  valor_centavos?: number;
  /** Presente quando `formato === "numero"`. */
  valor?: number;
  formato: FormatoKpiPainel;
  hint: string;
}

/**
 * Tipos de gráfico do painel: os quatro do `<PlanilhaChart />` mais `"tabela"`
 * (próximas faturas), que a visão geral desenha como tabela própria.
 */
export type TipoGraficoPainel = "donut" | "bar" | "line" | "area" | "tabela";

/** Um ponto de série: eixo X + chaves plotadas + extras de drill-down. */
export type PontoGraficoPainel = Record<string, string | number | null>;

/**
 * Gráfico ChartSeries-like do painel — mesmo contrato do módulo `/importar`,
 * mas com valores em CENTAVOS (`value_format: "centavos"`). No gráfico
 * `utilizacao-limite` o value é percentual ×100 (1350 = 13,50%).
 */
export interface GraficoPainel {
  id: string;
  kind: TipoGraficoPainel;
  title: string;
  x_key: string;
  series: Array<{ key: string; label: string }>;
  data: PontoGraficoPainel[];
  value_format: "centavos" | string;
  note: string;
}

/** Resposta completa de `GET /extratos/painel`. */
export interface Painel {
  kpis: KpiPainel[];
  graficos: GraficoPainel[];
  avisos: string[];
}

// --------------------------------------------------------------------------
// Conciliação
// --------------------------------------------------------------------------

export type PapelConciliacao = "pagamento" | "obrigacao";

export interface ItemConciliacao {
  transacao_id?: number | null;
  fatura_id?: number | null;
  papel: PapelConciliacao;
  valor_centavos?: number | null;
}

export interface SugestaoConciliacao {
  transacao_id: number;
  fatura_id: number;
  valor_centavos: number;
  motivo?: string;
  [extra: string]: unknown;
}

// --------------------------------------------------------------------------
// Erros da API
// --------------------------------------------------------------------------

/** Corpo de erro devolvido pela API de extratos (sempre em pt-BR). */
export interface ErroApi {
  erro: string;
}
