import type { Metadata, Viewport } from "next";
import "./globals.css";
import { AppStoreProvider } from "@/lib/store/app-store";
import { UsuarioProvider } from "@/components/auth/usuario-provider";
import { MolduraApp } from "@/components/auth/moldura-app";

export const metadata: Metadata = {
  title: {
    default: "celest.ia — Inteligência de Investimentos",
    template: "%s · celest.ia",
  },
  description:
    "Plataforma de análise financeira multi-agente: carteira consolidada, triagem fundamentalista com IA, perfil de investidor, rebalanceamento e Open Finance.",
};

export const viewport: Viewport = {
  themeColor: "#09090b",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="pt-BR" className="dark">
      <body className="min-h-screen bg-background antialiased">
        <UsuarioProvider>
          <AppStoreProvider>
            <MolduraApp>{children}</MolduraApp>
          </AppStoreProvider>
        </UsuarioProvider>
      </body>
    </html>
  );
}
