/**
 * Proteção de rotas de página: sem o cookie `celestia_sessao`, redireciona
 * (307) para `/entrar?voltar=<rota atual>`. A validade real da sessão é
 * checada pela API do motor (`/api/py/auth/eu`); aqui só evitamos renderizar
 * o app para quem claramente não entrou.
 *
 * Ficam de fora: `/entrar` (com cookie presente segue normal — a própria
 * página consulta `/auth/eu` e, se válido, volta para `voltar`), `/_next`,
 * `/api`, `/favicon.ico` e arquivos estáticos (qualquer caminho com extensão).
 */

import { NextResponse, type NextRequest } from "next/server";

const COOKIE_SESSAO = "celestia_sessao";

export function middleware(requisicao: NextRequest) {
  const { pathname, search } = requisicao.nextUrl;

  if (
    pathname === "/entrar" ||
    pathname.startsWith("/entrar/") ||
    pathname.startsWith("/_next") ||
    pathname.startsWith("/api") ||
    pathname === "/favicon.ico" ||
    /\.[a-zA-Z0-9]+$/.test(pathname)
  ) {
    return NextResponse.next();
  }

  const cookie = requisicao.cookies.get(COOKIE_SESSAO)?.value;
  if (cookie) {
    return NextResponse.next();
  }

  const destino = requisicao.nextUrl.clone();
  destino.pathname = "/entrar";
  destino.search = "";
  destino.searchParams.set("voltar", `${pathname}${search}`);
  return NextResponse.redirect(destino, 307);
}

export const config = {
  // Tudo menos _next, api, entrar, favicon e arquivos com extensão.
  matcher: ["/((?!_next/|api/|entrar|favicon\\.ico|.*\\.[a-zA-Z0-9]+$).*)"],
};
