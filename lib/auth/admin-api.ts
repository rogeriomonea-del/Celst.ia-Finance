/**
 * Cliente tipado da área de administração (`/api/py/auth/admin/*`).
 *
 * Propositalmente independente de `lib/auth/api.ts`: a tela `/admin` precisa
 * só do usuário atual (`/auth/eu`) e das rotas de administração, então a
 * chamada a `/auth/eu` é repetida aqui (duplicação mínima) para que este
 * módulo compile sozinho.
 *
 * Todas as respostas de erro do motor vêm como `{"erro": "mensagem pt-BR"}`
 * e viram `ErroAdmin` com `status` e mensagem pronta para exibir — inclusive
 * os 409 (tentar se rebaixar, remover o último admin…), que são mostrados
 * como chegam. Nada de e-mail, código ou token é logado aqui.
 */

/** Base das rotas de autenticação (reescrita para o motor Python). */
export const BASE_AUTH = "/api/py/auth";

// --------------------------------------------------------------------------
// Tipos
// --------------------------------------------------------------------------

/** Usuário da sessão atual (`/auth/eu`). */
export interface UsuarioAtual {
  id: number;
  email: string;
  nome: string | null;
  avatar_url: string | null;
  admin: boolean;
  ativo: boolean;
}

/** Conta como listada em `/auth/admin/usuarios`. Datas em UTC (`YYYY-MM-DD HH:MM:SS`). */
export interface ContaAdmin {
  id: number;
  email: string;
  nome: string | null;
  avatar_url: string | null;
  criado_em: string | null;
  ultimo_acesso: string | null;
  ativo: boolean;
  admin: boolean;
  sessoes_ativas: number;
  provedores: string[];
}

/** Sessão como listada em `/auth/admin/sessoes`. */
export interface SessaoAdmin {
  id: number;
  usuario_id: number;
  email: string;
  criada_em: string | null;
  expira_em: string | null;
  ultima_atividade: string | null;
  user_agent: string;
  ip: string | null;
  ativa: boolean;
}

/** Evento de acesso como listado em `/auth/admin/eventos`. */
export interface EventoAcesso {
  id: number;
  quando: string | null;
  email: string | null;
  usuario_id: number | null;
  acao: string;
  sucesso: boolean;
  detalhe: string | null;
  ip: string | null;
}

/** Campos aceitos pelo `PATCH /auth/admin/usuarios/<id>`. */
export interface AlteracaoConta {
  ativo?: boolean;
  admin?: boolean;
}

// --------------------------------------------------------------------------
// Erro
// --------------------------------------------------------------------------

const MENSAGENS_POR_STATUS: Record<number, string> = {
  400: "A requisição não foi aceita pelo servidor. Confira os dados e tente de novo.",
  401: "Sua sessão expirou. Entre de novo para continuar.",
  403: "Só administradores podem acessar esta área.",
  404: "O serviço de login não respondeu neste endereço. Se você está rodando localmente, suba o motor com python3 -m engine.server.",
  409: "A alteração entra em conflito com o estado atual da conta.",
  429: "Muitas requisições seguidas. Espere alguns segundos e tente de novo.",
  500: "O servidor encontrou um erro inesperado.",
};

const MENSAGEM_REDE =
  "Não foi possível falar com o serviço de login. Verifique se o motor está no ar (python3 -m engine.server) e tente de novo.";

/** Erro da API de administração, sempre com mensagem pt-BR exibível. */
export class ErroAdmin extends Error {
  /** HTTP status; 0 quando a rede falhou antes de qualquer resposta. */
  readonly status: number;

  constructor(mensagem: string, status: number) {
    super(mensagem);
    this.name = "ErroAdmin";
    this.status = status;
  }

  /** true quando o motor nem respondeu (fora do ar / sem rede). */
  get backendIndisponivel(): boolean {
    return this.status === 0 || this.status === 404 || this.status === 502;
  }
}

function mensagemDeErro(corpo: unknown, status: number): string {
  if (corpo && typeof corpo === "object") {
    const erro = (corpo as Record<string, unknown>).erro;
    if (typeof erro === "string" && erro.trim() !== "") return erro;
  }
  return MENSAGENS_POR_STATUS[status] ?? MENSAGEM_REDE;
}

// --------------------------------------------------------------------------
// Núcleo de fetch
// --------------------------------------------------------------------------

async function requisitar<T>(
  metodo: "GET" | "POST" | "PATCH" | "DELETE",
  caminho: string,
  opcoes?: { params?: URLSearchParams; corpo?: unknown; signal?: AbortSignal }
): Promise<T> {
  const query = opcoes?.params?.toString() ?? "";
  const url = query ? `${BASE_AUTH}${caminho}?${query}` : `${BASE_AUTH}${caminho}`;

  let resposta: Response;
  try {
    resposta = await fetch(url, {
      method: metodo,
      credentials: "include",
      cache: "no-store",
      signal: opcoes?.signal,
      headers: { Accept: "application/json" },
      ...(opcoes?.corpo !== undefined
        ? {
            body: JSON.stringify(opcoes.corpo),
            headers: { Accept: "application/json", "Content-Type": "application/json" },
          }
        : {}),
    });
  } catch (falha) {
    // Abort não é erro de backend: repropaga para quem cancelou.
    if (falha instanceof DOMException && falha.name === "AbortError") throw falha;
    throw new ErroAdmin(MENSAGEM_REDE, 0);
  }

  const corpo: unknown = await resposta.json().catch(() => null);
  if (!resposta.ok) {
    throw new ErroAdmin(mensagemDeErro(corpo, resposta.status), resposta.status);
  }
  return corpo as T;
}

// --------------------------------------------------------------------------
// Usuário atual
// --------------------------------------------------------------------------

/** `GET /auth/eu` — lança `ErroAdmin` com status 401 quando não há sessão. */
export async function obterUsuarioAtual(signal?: AbortSignal): Promise<UsuarioAtual> {
  const resposta = await requisitar<{ usuario: UsuarioAtual }>("GET", "/eu", { signal });
  return resposta.usuario;
}

// --------------------------------------------------------------------------
// Contas
// --------------------------------------------------------------------------

/** Todas as contas, pendentes primeiro (ordem definida pelo motor). */
export async function listarContas(signal?: AbortSignal): Promise<ContaAdmin[]> {
  const resposta = await requisitar<{ usuarios: ContaAdmin[] }>("GET", "/admin/usuarios", {
    signal,
  });
  return Array.isArray(resposta?.usuarios) ? resposta.usuarios : [];
}

/** Aprova/desativa ou promove/rebaixa uma conta. 409 vem com a explicação do motor. */
export async function alterarConta(
  id: number,
  alteracao: AlteracaoConta
): Promise<UsuarioAtual> {
  const resposta = await requisitar<{ usuario: UsuarioAtual }>(
    "PATCH",
    `/admin/usuarios/${id}`,
    { corpo: alteracao }
  );
  return resposta.usuario;
}

/** Derruba a conta de todos os dispositivos. */
export async function encerrarSessoesDaConta(
  id: number
): Promise<{ ok: boolean; sessoes_encerradas: number }> {
  return requisitar("POST", `/admin/usuarios/${id}/encerrar-sessoes`);
}

// --------------------------------------------------------------------------
// Sessões
// --------------------------------------------------------------------------

/** Sessões ativas (de todas as contas ou só de `usuarioId`). */
export async function listarSessoes(
  usuarioId?: number,
  signal?: AbortSignal
): Promise<SessaoAdmin[]> {
  const params = new URLSearchParams();
  if (usuarioId !== undefined) params.set("usuario_id", String(usuarioId));
  const resposta = await requisitar<{ sessoes: SessaoAdmin[] }>("GET", "/admin/sessoes", {
    params,
    signal,
  });
  return Array.isArray(resposta?.sessoes) ? resposta.sessoes : [];
}

/** Encerra UMA sessão. 404 se já não existia. */
export async function encerrarSessao(id: number): Promise<{ ok: boolean }> {
  return requisitar("DELETE", `/admin/sessoes/${id}`);
}

// --------------------------------------------------------------------------
// Eventos de acesso
// --------------------------------------------------------------------------

/** Eventos mais recentes, com filtro opcional por e-mail exato. */
export async function listarEventos(
  opcoes?: { limite?: number; email?: string },
  signal?: AbortSignal
): Promise<EventoAcesso[]> {
  const params = new URLSearchParams();
  params.set("limite", String(opcoes?.limite ?? 100));
  const email = opcoes?.email?.trim();
  if (email) params.set("email", email);
  const resposta = await requisitar<{ eventos: EventoAcesso[] }>("GET", "/admin/eventos", {
    params,
    signal,
  });
  return Array.isArray(resposta?.eventos) ? resposta.eventos : [];
}
