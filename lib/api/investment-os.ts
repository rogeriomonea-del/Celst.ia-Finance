/**
 * Cliente tipado da API do Investment Intelligence OS (backend FastAPI do
 * repositório irmão `Celest.ia-v2-Alpha`). Contrato resumido em
 * `docs/INTEGRATION.md` (Fase 5).
 *
 * Regras: o frontend NUNCA calcula indicadores nem inventa dados. Sem mock:
 * API indisponível vira estado de erro explícito na UI.
 */

export const IIOS_API_URL =
  process.env.NEXT_PUBLIC_IIOS_API_URL ?? "http://localhost:8000";

const BASE = `${IIOS_API_URL.replace(/\/+$/, "")}/v1`;

export const BACKEND_START_COMMAND = "uvicorn investment_os.api.main:app";

// ---------------------------------------------------------------------------
// Erros
// ---------------------------------------------------------------------------

export class IIOSApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "IIOSApiError";
    this.status = status;
    this.code = code;
  }
}

export class IIOSUnreachableError extends Error {
  constructor() {
    super(
      `Não foi possível conectar à API do backend em ${IIOS_API_URL}. ` +
        `Inicie o backend: ${BACKEND_START_COMMAND}`
    );
    this.name = "IIOSUnreachableError";
  }
}

export function isApiError(error: unknown, code?: string): error is IIOSApiError {
  return (
    error instanceof IIOSApiError && (code === undefined || error.code === code)
  );
}

export function isUnreachableError(
  error: unknown
): error is IIOSUnreachableError {
  return error instanceof IIOSUnreachableError;
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return "Erro inesperado ao comunicar com a API do backend.";
}

// ---------------------------------------------------------------------------
// Tipos — espelham o contrato /v1 do backend
// ---------------------------------------------------------------------------

export type Confianca = "BAIXA" | "MEDIA" | "ALTA";

export interface ProfileQuestion {
  id: number;
  dimension: string;
  text: string;
  options: string[];
}

export interface ProfileAssessment {
  assessment_id: number;
  scores: Record<string, number>;
  conflicts: string[];
  pending_questions: ProfileQuestion[];
  confidence: Confianca;
}

export interface LatestAssessment {
  assessment_id: number;
  scores: Record<string, number>;
  conflicts: string[];
  confidence: Confianca;
  created_at: string;
}

export type PolicyStatus = "draft" | "confirmed" | "superseded";

/** Conteúdo da IPS: JSON estruturado do backend (editável como texto na UI). */
export type PolicyContent = Record<string, unknown>;

export interface PolicyDraftResult {
  policy_id: number;
  version_id: number;
  version: number;
  status: "draft";
  content: PolicyContent;
}

export interface PolicyVersion {
  version_id: number;
  version: number;
  status: PolicyStatus;
  created_at: string;
  author: string | null;
  reason: string | null;
  prev_version: string | number | null;
  confirmed_at: string | null;
  content: PolicyContent;
}

export interface PolicyConfirmResult {
  version_id: number;
  version: number;
  status: "confirmed";
}

export interface ConfirmedPolicy {
  version_id: number;
  version: number;
  content: PolicyContent;
  confirmed_at: string;
}

export type ImportRowStatus =
  | "ok"
  | "ambiguous"
  | "unknown"
  | "rejected"
  | "duplicate";

export type CostStatus = "conhecido" | "desconhecido";

export type AssetClassApi =
  | "acao_br"
  | "fii"
  | "bdr"
  | "etf"
  | "renda_fixa"
  | "outro";

/** Resolução de instrumento retornada pelo backend (objeto, pode ser `{}`). */
export interface InstrumentResolution {
  status?: "ok" | "ambiguous" | "unknown";
  confidence?: Confianca;
  method?: string;
  reason?: string;
  ticker?: string;
  cnpj?: string | null;
  classe?: string | null;
  cd_cvm?: string | null;
  setor?: string | null;
  asset_class?: string | null;
  asset_class_hint?: string | null;
}

export interface ImportRow {
  row_id: number;
  row_index: number;
  status: ImportRowStatus;
  reason: string | null;
  confidence: Confianca | null;
  ticker: string | null;
  quantity: number | null;
  avg_cost: number | null;
  cost_status: CostStatus;
  currency: string | null;
  data_base: string | null;
  resolution: InstrumentResolution | null;
}

/** Resumo legível da resolução (método + motivo), nunca o objeto cru. */
export function resolutionToLabel(res: InstrumentResolution | null): string | null {
  if (!res || Object.keys(res).length === 0) return null;
  const parts: string[] = [];
  if (res.method === "fca_listing") parts.push("listagem oficial FCA/CVM");
  else if (res.method === "user_override") parts.push("confirmado manualmente");
  else if (res.method === "unresolved") parts.push("não resolvido");
  if (res.asset_class) parts.push(`classe ${res.asset_class}`);
  if (res.reason) parts.push(res.reason);
  return parts.length ? parts.join(" · ") : null;
}

export interface ImportPreview {
  import_id: number;
  status: string;
  adapter: string;
  file_sha256: string;
  data_base: string | null;
  pii_removed_count: number;
  counts: Record<string, number>;
  rows: ImportRow[];
  requires_user_confirmation: boolean;
}

export interface ImportConfirmResult {
  snapshot_id: number;
  version: number;
  idempotent: boolean;
  excluded_rows?: number | Array<string | number>;
}

export interface SnapshotSummary {
  id: number;
  version: number;
  created_at: string;
  data_base: string | null;
}

export interface SnapshotDetail {
  snapshot: SnapshotSummary & Record<string, unknown>;
  positions: Array<Record<string, unknown>>;
}

export type Severidade = "critica" | "nao_critica";

export interface AnalysisPosition {
  ticker: string;
  asset_class: string;
  quantity: number;
  avg_cost: number | null;
  cost_status: CostStatus;
  price?: number | null;
  price_date?: string | null;
  price_staleness_days?: number | null;
  market_value: number | null;
  weight_pct?: number | null;
  price_status: string;
  setor: string | null;
  natureza?: string | null;
}

export interface PolicyViolation {
  tipo: string;
  chave: string;
  peso_pct: number;
  severidade: Severidade;
  faixa?: { min_pct: number; max_pct: number } | null;
  limite_pct?: number | null;
}

export interface QualidadeDados {
  posicoes_total: number;
  posicoes_precificadas: number;
  cobertura_pct: number;
  sem_preco: string[];
  custo_desconhecido: string[];
  precos_stale_7d: string[];
}

export interface SnapshotAnalysis {
  patrimonio_precificado_brl: number | null;
  nota_patrimonio: string;
  pesos: {
    por_ativo: Record<string, number>;
    por_emissor: Record<string, number>;
    por_setor: Record<string, number>;
    por_classe: Record<string, number>;
    por_moeda: Record<string, number>;
    por_pais: Record<string, number>;
  };
  posicoes: AnalysisPosition[];
  concentracao: {
    maior_posicao_pct: number | null;
    hhi: number | null;
  };
  violacoes: PolicyViolation[];
  qualidade_dados: QualidadeDados;
  confianca: string;
  fontes: string | string[] | Record<string, string>;
  data_base_carteira: string | null;
  data_analise: string;
}

export interface RebalanceAction {
  seq: number;
  scope: string;
  key: string;
  action: string;
  priority: number | string;
  amount_brl: number | null;
  current_pct?: number | null;
  target_min_pct?: number | null;
  target_max_pct?: number | null;
  rationale: string;
  revisao?: string | null;
  custo_imposto?: string | null;
}

export interface RebalancePlan {
  plan_id: number;
  aporte_mensal_brl: number;
  premissas: string[];
  proximo_aporte: Record<string, number>;
  plano_3_meses: Array<Record<string, number>>;
  plano_6_meses: Array<Record<string, number>>;
  caminho_ate_faixas: {
    dentro_das_faixas_apos_simulacao: boolean;
    pesos_projetados_pct: Record<string, number>;
  };
  vendas_evitadas_brl: number;
  concentracao_antes: {
    maior_posicao_pct?: number | null;
    hhi?: number | null;
  } | null;
  exposicao_cambial_antes: Record<string, number> | number | null;
  violacoes_remanescentes: PolicyViolation[];
  acoes: RebalanceAction[];
  confianca: string;
  qualidade_dados: QualidadeDados;
}

export interface PortfolioIssue {
  id: number;
  created_at: string;
  scope: string;
  severity: string;
  description: string;
}

// ---------------------------------------------------------------------------
// Núcleo de requisições
// ---------------------------------------------------------------------------

interface ErrorDetailShape {
  code?: string;
  message?: string;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, { cache: "no-store", ...init });
  } catch {
    throw new IIOSUnreachableError();
  }

  if (!response.ok) {
    let code = "erro_desconhecido";
    let message = `A API do backend respondeu com erro ${response.status}.`;
    try {
      const body = (await response.json()) as {
        detail?: ErrorDetailShape | string;
      };
      if (typeof body.detail === "string") {
        message = body.detail;
      } else if (body.detail) {
        code = body.detail.code ?? code;
        message = body.detail.message ?? message;
      }
    } catch {
      // corpo não-JSON: mantém a mensagem genérica segura
    }
    throw new IIOSApiError(response.status, code, message);
  }

  return (await response.json()) as T;
}

function postJson<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

// ---------------------------------------------------------------------------
// Perfil
// ---------------------------------------------------------------------------

export function fetchProfileQuestions(
  answers: Record<string, string>
): Promise<ProfileQuestion[]> {
  return postJson<ProfileQuestion[]>("/profile/questions", { answers });
}

export function assessProfile(
  answers: Record<string, string>
): Promise<ProfileAssessment> {
  return postJson<ProfileAssessment>("/profile/assess", { answers });
}

/** Retorna `null` quando ainda não existe avaliação (404 `no_assessment`). */
export async function fetchLatestAssessment(): Promise<LatestAssessment | null> {
  try {
    return await request<LatestAssessment>("/profile/assessment/latest");
  } catch (error) {
    if (isApiError(error) && error.status === 404) return null;
    throw error;
  }
}

// ---------------------------------------------------------------------------
// Política (IPS)
// ---------------------------------------------------------------------------

export function draftPolicy(input: {
  reason: string;
  author?: string;
  content?: PolicyContent;
}): Promise<PolicyDraftResult> {
  return postJson<PolicyDraftResult>("/policy/draft", input);
}

export function fetchPolicyVersions(): Promise<PolicyVersion[]> {
  return request<PolicyVersion[]>("/policy/versions");
}

export function confirmPolicy(versionId: number): Promise<PolicyConfirmResult> {
  return request<PolicyConfirmResult>(
    `/policy/${encodeURIComponent(versionId)}/confirm`,
    { method: "POST" }
  );
}

/** Retorna `null` quando não há IPS confirmada (404 `no_confirmed_policy`). */
export async function fetchConfirmedPolicy(): Promise<ConfirmedPolicy | null> {
  try {
    return await request<ConfirmedPolicy>("/policy/confirmed");
  } catch (error) {
    if (isApiError(error) && error.status === 404) return null;
    throw error;
  }
}

// ---------------------------------------------------------------------------
// Importação de carteira (B3)
// ---------------------------------------------------------------------------

export const IMPORT_MAX_FILE_BYTES = 10 * 1024 * 1024; // 10 MB

export function importPortfolio(file: File): Promise<ImportPreview> {
  const form = new FormData();
  form.append("file", file);
  // Sem Content-Type manual: o browser define o boundary do multipart.
  return request<ImportPreview>("/portfolio/import", {
    method: "POST",
    body: form,
  });
}

export function fetchImportPreview(importId: number): Promise<ImportPreview> {
  return request<ImportPreview>(
    `/portfolio/import/${encodeURIComponent(importId)}/preview`
  );
}

export function correctImportRow(
  importId: number,
  rowId: number,
  correction: { ticker: string; asset_class?: AssetClassApi }
): Promise<ImportPreview> {
  return postJson<ImportPreview>(
    `/portfolio/import/${encodeURIComponent(importId)}/rows/${encodeURIComponent(
      rowId
    )}/correct`,
    correction
  );
}

export function confirmImport(
  importId: number,
  acceptPartial: boolean
): Promise<ImportConfirmResult> {
  return postJson<ImportConfirmResult>(
    `/portfolio/import/${encodeURIComponent(importId)}/confirm`,
    { accept_partial: acceptPartial }
  );
}

export function excludedRowsCount(
  excluded: ImportConfirmResult["excluded_rows"]
): number {
  if (excluded === undefined || excluded === null) return 0;
  if (typeof excluded === "number") return excluded;
  return excluded.length;
}

// ---------------------------------------------------------------------------
// Carteira / snapshots
// ---------------------------------------------------------------------------

export function fetchSnapshots(): Promise<SnapshotSummary[]> {
  return request<SnapshotSummary[]>("/portfolio/snapshots");
}

export function fetchSnapshot(id: number): Promise<SnapshotDetail> {
  return request<SnapshotDetail>(
    `/portfolio/snapshots/${encodeURIComponent(id)}`
  );
}

export function fetchSnapshotAnalysis(id: number): Promise<SnapshotAnalysis> {
  return request<SnapshotAnalysis>(
    `/portfolio/snapshots/${encodeURIComponent(id)}/analysis`
  );
}

export function rebalanceSnapshot(id: number,
  months?: number
): Promise<RebalancePlan> {
  return postJson<RebalancePlan>(
    `/portfolio/snapshots/${encodeURIComponent(id)}/rebalance`,
    months === undefined ? {} : { months }
  );
}

export function fetchPortfolioIssues(): Promise<PortfolioIssue[]> {
  return request<PortfolioIssue[]>("/portfolio/issues");
}

// ---------------------------------------------------------------------------
// Utilitários de exibição (não calculam indicadores; só normalizam formatos)
// ---------------------------------------------------------------------------

/** Normaliza `fontes` (string, lista ou mapa) para uma lista exibível. */
export function fontesToList(
  fontes: SnapshotAnalysis["fontes"] | undefined | null
): string[] {
  if (!fontes) return [];
  if (typeof fontes === "string") return fontes.split(";").map((s) => s.trim()).filter(Boolean);
  if (Array.isArray(fontes)) return fontes.map(String);
  return Object.entries(fontes).map(([key, value]) => `${key}: ${String(value)}`);
}

/** Formata a faixa `{min_pct, max_pct}` de uma violação como texto. */
export function faixaToLabel(faixa: PolicyViolation["faixa"]): string | null {
  if (!faixa || typeof faixa !== "object") return null;
  return `${faixa.min_pct ?? "?"}–${faixa.max_pct ?? "?"}%`;
}
