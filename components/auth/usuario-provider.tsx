"use client";

/**
 * Contexto do usuário logado. Carrega `/auth/eu` uma vez no cliente e expõe
 * `{usuario, carregando, sair, recarregar, definirUsuario}` via `useUsuario()`.
 *
 * Sem sessão (401) fora de `/entrar`, manda para `/entrar?voltar=<rota atual>`.
 * Falha de rede não redireciona: o app fica sem usuário e as telas que
 * dependem da API mostram o próprio aviso de "motor fora do ar".
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { usePathname, useRouter } from "next/navigation";
import {
  ErroAuth,
  eu,
  sair as sairNaApi,
  urlEntrar,
  type Usuario,
} from "@/lib/auth/api";

export interface ContextoUsuario {
  /** Usuário autenticado, ou `null` enquanto carrega / sem sessão. */
  usuario: Usuario | null;
  /** true até a primeira resposta de `/auth/eu`. */
  carregando: boolean;
  /** Encerra a sessão no motor e limpa o estado local (não navega). */
  sair: () => Promise<void>;
  /** Consulta `/auth/eu` de novo (ex.: depois de um login). */
  recarregar: () => Promise<Usuario | null>;
  /** Define o usuário localmente (ex.: logo após `verificarCodigo`). */
  definirUsuario: (usuario: Usuario | null) => void;
}

const Contexto = createContext<ContextoUsuario | null>(null);

function ehPaginaDeEntrada(pathname: string | null): boolean {
  return pathname === "/entrar" || (pathname ?? "").startsWith("/entrar/");
}

export function UsuarioProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [usuario, setUsuario] = useState<Usuario | null>(null);
  const [carregando, setCarregando] = useState(true);
  const pathnameRef = useRef(pathname);
  pathnameRef.current = pathname;

  const recarregar = useCallback(async (): Promise<Usuario | null> => {
    try {
      const atual = await eu();
      setUsuario(atual);
      return atual;
    } catch (falha) {
      setUsuario(null);
      if (
        falha instanceof ErroAuth &&
        falha.naoAutenticado &&
        !ehPaginaDeEntrada(pathnameRef.current)
      ) {
        router.replace(urlEntrar());
      }
      return null;
    } finally {
      setCarregando(false);
    }
  }, [router]);

  useEffect(() => {
    let ativo = true;
    const controlador = new AbortController();
    eu(controlador.signal)
      .then((atual) => {
        if (ativo) setUsuario(atual);
      })
      .catch((falha: unknown) => {
        if (!ativo) return;
        if (falha instanceof DOMException && falha.name === "AbortError") return;
        setUsuario(null);
        if (
          falha instanceof ErroAuth &&
          falha.naoAutenticado &&
          !ehPaginaDeEntrada(pathnameRef.current)
        ) {
          router.replace(urlEntrar());
        }
      })
      .finally(() => {
        if (ativo) setCarregando(false);
      });
    return () => {
      ativo = false;
      controlador.abort();
    };
    // Só na montagem: a sessão vale para toda a navegação do app.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const sair = useCallback(async () => {
    try {
      await sairNaApi();
    } catch {
      // Mesmo que o motor não responda, o estado local é limpo; o cookie
      // (se ainda válido) será rejeitado pelo middleware/API na próxima vez.
    } finally {
      setUsuario(null);
    }
  }, []);

  const definirUsuario = useCallback((novo: Usuario | null) => {
    setUsuario(novo);
    setCarregando(false);
  }, []);

  const valor = useMemo<ContextoUsuario>(
    () => ({ usuario, carregando, sair, recarregar, definirUsuario }),
    [usuario, carregando, sair, recarregar, definirUsuario]
  );

  return <Contexto.Provider value={valor}>{children}</Contexto.Provider>;
}

/** Acesso ao usuário logado. Precisa estar dentro de `<UsuarioProvider>`. */
export function useUsuario(): ContextoUsuario {
  const contexto = useContext(Contexto);
  if (!contexto) {
    throw new Error("useUsuario() precisa ser usado dentro de <UsuarioProvider>.");
  }
  return contexto;
}
