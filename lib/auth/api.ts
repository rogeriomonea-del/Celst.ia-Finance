/**
 * Cliente tipado da API de autenticação (`/api/py/auth/*`).
 *
 * Em dev o rewrite de `next.config.mjs` manda para `python3 -m engine.server`;
 * em produção o nginx faz proxy para o mesmo motor. O cookie de sessão
 * (`celestia_sessao`, HttpOnly) é enviado automaticamente por ser same-origin;
 * `credentials: "include"` fica explícito por clareza. Todas as respostas de
 * erro vêm como `{"erro": "mensagem em pt-BR"}` e viram `ErroAuth`.
 *
 * Nunca registre e-mail, código ou token no console.
 */

/** Base de todas as chamadas de autenticação. */
export const BASE_AUTH = "/api/py/auth";

/** Nome do cookie de sessão emitido pelo motor (lido só pelo middleware). */
export const COOKIE_SESSAO = "celestia_sessao";

/** Usuário autenticado, como o motor devolve em `/auth/eu`. */
export interface Usuario {
  id: number;
  email: string;
  nome: string;
  avatar_url: string | null;
  admin: boolean;
  ativo: boolean;
}

/** Resposta de `/auth/config`. */
export interface ConfigAuth {
  codigo_email: boolean;
  google: boolean;
  /** `false` ⇒ SMTP não configurado: o código sai no log do servidor. */
  envio_real: boolean;
  url_base: string;
}

/** Resposta de `/auth/codigo`. */
export interface CodigoEnviado {
  enviado: boolean;
  /** `"log"` quando não há SMTP e o código foi gravado no log do servidor. */
  modo: "email" | "log";
  mensagem: string;
}

const MENSAGENS_POR_STATUS: Record<number, string> = {
  400: "A requisição não foi aceita. Confira os dados e tente de novo.",
  401: "Sua sessão expirou ou você ainda não entrou.",
  403: "Sua conta foi criada e aguarda aprovação do administrador.",
  404: "O serviço de login não respondeu neste endereço. Se você está rodando localmente, suba o motor com python3 -m engine.server.",
  429: "Muitas tentativas seguidas. Espere alguns minutos e tente de novo.",
  500: "O serviço de login encontrou um erro inesperado.",
  501: "Login com Google não está configurado neste servidor.",
  502: "Não foi possível enviar o e-mail agora. Tente novamente em instantes.",
  503: "O serviço de login está temporariamente indisponível.",
};

const MENSAGEM_PADRAO =
  "Não foi possível falar com o serviço de login. Verifique sua conexão e tente de novo.";

/** Erro da API de autenticação, sempre com mensagem pt-BR pronta para exibição. */
export class ErroAuth extends Error {
  /** HTTP status; 0 quando a rede falhou antes de qualquer resposta. */
  readonly status: number;

  constructor(mensagem: string, status: number) {
    super(mensagem);
    this.name = "ErroAuth";
    this.status = status;
  }

  /** true quando o backend nem respondeu (motor fora do ar / sem rede). */
  get backendIndisponivel(): boolean {
    return this.status === 0 || this.status === 404 || this.status === 502;
  }

  /** true para 401: não há sessão válida. */
  get naoAutenticado(): boolean {
    return this.status === 401;
  }

  /** true para 403: conta aguardando aprovação ou desativada. */
  get contaInativa(): boolean {
    return this.status === 403;
  }
}

function mensagemDeErro(corpo: unknown, status: number): string {
  if (
    typeof corpo === "object" &&
    corpo !== null &&
    typeof (corpo as Record<string, unknown>).erro === "string" &&
    ((corpo as Record<string, unknown>).erro as string).trim() !== ""
  ) {
    return (corpo as Record<string, string>).erro;
  }
  return MENSAGENS_POR_STATUS[status] ?? MENSAGEM_PADRAO;
}

async function requisitar<T>(
  metodo: "GET" | "POST",
  caminho: string,
  corpo?: unknown,
  signal?: AbortSignal
): Promise<T> {
  let resposta: Response;
  try {
    resposta = await fetch(`${BASE_AUTH}${caminho}`, {
      method: metodo,
      credentials: "include",
      cache: "no-store",
      signal,
      ...(corpo !== undefined
        ? {
            body: JSON.stringify(corpo),
            headers: { "Content-Type": "application/json" },
          }
        : {}),
    });
  } catch (falha) {
    if (falha instanceof DOMException && falha.name === "AbortError") throw falha;
    throw new ErroAuth(MENSAGEM_PADRAO, 0);
  }

  const dados: unknown = await resposta.json().catch(() => null);
  if (!resposta.ok) {
    throw new ErroAuth(mensagemDeErro(dados, resposta.status), resposta.status);
  }
  return dados as T;
}

// --------------------------------------------------------------------------
// Endpoints tipados
// --------------------------------------------------------------------------

/** Configuração pública do login (Google ativo? SMTP real?). */
export function obterConfig(signal?: AbortSignal): Promise<ConfigAuth> {
  return requisitar<ConfigAuth>("GET", "/config", undefined, signal);
}

/** Pede um código de 6 dígitos para o e-mail informado. */
export function pedirCodigo(email: string): Promise<CodigoEnviado> {
  return requisitar<CodigoEnviado>("POST", "/codigo", { email: email.trim() });
}

/** Troca e-mail + código por uma sessão (cookie). 403 = conta pendente/desativada. */
export async function verificarCodigo(email: string, codigo: string): Promise<Usuario> {
  const dados = await requisitar<{ usuario: Usuario }>("POST", "/verificar", {
    email: email.trim(),
    codigo: codigo.trim(),
  });
  return dados.usuario;
}

/** Quem está logado; lança `ErroAuth` 401 sem sessão. */
export async function eu(signal?: AbortSignal): Promise<Usuario> {
  const dados = await requisitar<{ usuario: Usuario }>("GET", "/eu", undefined, signal);
  return dados.usuario;
}

/** Encerra a sessão atual (o motor zera o cookie). */
export function sair(): Promise<{ ok: boolean }> {
  return requisitar<{ ok: boolean }>("POST", "/sair", {});
}

/** Encerra todas as sessões da conta, em todos os dispositivos. */
export function sairDeTudo(): Promise<{ ok: boolean; sessoes_encerradas: number }> {
  return requisitar<{ ok: boolean; sessoes_encerradas: number }>("POST", "/sair-de-tudo", {});
}

/** URL que inicia o login com Google (navegar com `window.location`, não `fetch`). */
export function urlLoginGoogle(destino: string): string {
  return `${BASE_AUTH}/google?destino=${encodeURIComponent(destino)}`;
}

/**
 * Normaliza o `?voltar=` para um caminho interno seguro: aceita só o que
 * começa com `/` (e não `//`), para não virar redirecionamento aberto.
 */
export function destinoSeguro(voltar: string | null | undefined): string {
  const texto = (voltar ?? "").trim();
  if (!texto.startsWith("/") || texto.startsWith("//") || texto.startsWith("/\\")) {
    return "/";
  }
  if (texto === "/entrar" || texto.startsWith("/entrar?")) return "/";
  return texto;
}

/** Monta `/entrar?voltar=<rota atual>` a partir da localização do navegador. */
export function urlEntrar(voltar?: string): string {
  const destino =
    voltar ??
    (typeof window !== "undefined"
      ? window.location.pathname + window.location.search
      : "/");
  return `/entrar?voltar=${encodeURIComponent(destinoSeguro(destino))}`;
}

/** Iniciais para o avatar (nome ou e-mail), ex.: "Ana Souza" → "AS". */
export function iniciaisDe(usuario: Pick<Usuario, "nome" | "email">): string {
  const base = (usuario.nome || usuario.email || "").trim();
  if (!base) return "?";
  const partes = base.split(/[\s@._-]+/).filter(Boolean);
  if (partes.length >= 2) {
    return (partes[0][0] + partes[1][0]).toUpperCase();
  }
  return base.slice(0, 2).toUpperCase();
}
