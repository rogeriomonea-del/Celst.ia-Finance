"use client";

/**
 * Moldura do app: sidebar + área de conteúdo em todas as páginas, exceto em
 * `/entrar`, que ocupa a tela inteira sem menu (o usuário ainda não entrou).
 */

import { usePathname } from "next/navigation";
import { Sidebar } from "@/components/sidebar";

export function MolduraApp({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const telaDeEntrada = pathname === "/entrar" || pathname.startsWith("/entrar/");

  if (telaDeEntrada) {
    return <>{children}</>;
  }

  return (
    <>
      <Sidebar />
      <main className="min-h-screen pt-14 lg:pl-64 lg:pt-0">
        <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-10">
          {children}
        </div>
      </main>
    </>
  );
}
