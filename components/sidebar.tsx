"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Bot,
  FileSpreadsheet,
  Landmark,
  LayoutDashboard,
  Loader2,
  LogOut,
  Menu,
  Scale,
  ShieldCheck,
  Sparkles,
  UserRound,
  Wallet,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useUsuario } from "@/components/auth/usuario-provider";
import { AvatarUsuario } from "@/components/auth/avatar-usuario";

interface NavItem {
  href: string;
  label: string;
  description: string;
  icon: typeof LayoutDashboard;
}

const NAV_ITEMS: NavItem[] = [
  {
    href: "/",
    label: "Dashboard",
    description: "Carteira e indicadores",
    icon: LayoutDashboard,
  },
  {
    href: "/importar",
    label: "Importe suas Planilhas",
    description: "Excel/CSV vira análise",
    icon: FileSpreadsheet,
  },
  {
    href: "/extratos",
    label: "Extratos & Cartões",
    description: "Bancos, cartões e faturas",
    icon: Landmark,
  },
  {
    href: "/agentes",
    label: "Agentes IA",
    description: "Análise multi-agente",
    icon: Bot,
  },
  {
    href: "/perfil",
    label: "Perfil",
    description: "Suitability do investidor",
    icon: UserRound,
  },
  {
    href: "/rebalanceamento",
    label: "Rebalanceamento",
    description: "Ajustes de alocação",
    icon: Scale,
  },
  {
    href: "/organizador",
    label: "Organizador",
    description: "Fluxo de caixa · Open Finance",
    icon: Wallet,
  },
];

/** Item visível só para administradores (aprova contas, vê sessões e acessos). */
const ITEM_ADMIN: NavItem = {
  href: "/admin",
  label: "Administração",
  description: "Contas, sessões e acessos",
  icon: ShieldCheck,
};

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  const { usuario } = useUsuario();
  const itens = usuario?.admin ? [...NAV_ITEMS, ITEM_ADMIN] : NAV_ITEMS;

  return (
    <nav className="flex flex-1 flex-col gap-1 px-3">
      {itens.map((item) => {
        const isActive =
          item.href === "/"
            ? pathname === "/"
            : pathname.startsWith(item.href);
        const Icon = item.icon;
        return (
          <Link
            key={item.href}
            href={item.href}
            onClick={onNavigate}
            className={cn(
              "group flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors",
              isActive
                ? "bg-white/[0.07] text-foreground"
                : "text-muted-foreground hover:bg-white/[0.04] hover:text-foreground"
            )}
          >
            <Icon
              className={cn(
                "h-4 w-4 shrink-0 transition-colors",
                isActive ? "text-primary" : "text-muted-foreground group-hover:text-foreground"
              )}
            />
            <span className="flex flex-col">
              <span className="font-medium leading-tight">{item.label}</span>
              <span className="text-[11px] leading-tight text-muted-foreground/70">
                {item.description}
              </span>
            </span>
          </Link>
        );
      })}
    </nav>
  );
}

function BrandMark() {
  return (
    <div className="flex items-center gap-2.5 px-6 py-6">
      <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-primary/15 ring-1 ring-primary/30">
        <Sparkles className="h-4 w-4 text-primary" />
      </div>
      <div className="flex flex-col">
        <span className="text-sm font-semibold tracking-tight">celest.ia</span>
        <span className="text-[11px] text-muted-foreground">
          Inteligência de Investimentos
        </span>
      </div>
    </div>
  );
}

/** Rodapé com avatar/iniciais, nome ou e-mail e botão "Sair". */
function RodapeUsuario({ onNavigate }: { onNavigate?: () => void }) {
  const router = useRouter();
  const { usuario, carregando, sair } = useUsuario();
  const [saindo, setSaindo] = useState(false);

  const aoSair = async () => {
    setSaindo(true);
    try {
      await sair();
    } finally {
      onNavigate?.();
      router.replace("/entrar");
    }
  };

  return (
    <div className="border-t border-white/[0.06] px-4 py-4">
      {carregando ? (
        <div className="flex items-center gap-3 px-2">
          <Skeleton className="h-9 w-9 rounded-full" />
          <div className="flex-1 space-y-1.5">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-2.5 w-32" />
          </div>
        </div>
      ) : usuario ? (
        <div className="flex items-center gap-3 px-2">
          <AvatarUsuario usuario={usuario} />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium leading-tight" title={usuario.nome || usuario.email}>
              {usuario.nome || usuario.email}
            </p>
            <p className="truncate text-[11px] leading-tight text-muted-foreground" title={usuario.email}>
              {usuario.nome ? usuario.email : usuario.admin ? "Administrador" : "Conta ativa"}
            </p>
          </div>
          <Button
            variant="ghost"
            size="icon"
            aria-label="Sair"
            title="Sair"
            onClick={() => void aoSair()}
            disabled={saindo}
            className="h-8 w-8 shrink-0 text-muted-foreground hover:text-foreground"
          >
            {saindo ? (
              <Loader2 className="animate-spin" aria-hidden="true" />
            ) : (
              <LogOut aria-hidden="true" />
            )}
          </Button>
        </div>
      ) : (
        <Button asChild variant="outline" size="sm" className="w-full">
          <Link href="/entrar" onClick={onNavigate}>
            Entrar
          </Link>
        </Button>
      )}
      <p className="mt-3 px-2 text-[11px] leading-relaxed text-muted-foreground/70">
        v2.0 Alpha · Dados via brapi.dev
        <br />
        Uso educacional — não é recomendação.
      </p>
    </div>
  );
}

export function Sidebar() {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <>
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-64 flex-col border-r border-white/[0.06] bg-surface lg:flex">
        <BrandMark />
        <NavLinks />
        <RodapeUsuario />
      </aside>

      <header className="fixed inset-x-0 top-0 z-40 flex h-14 items-center justify-between border-b border-white/[0.06] bg-background/80 px-4 backdrop-blur lg:hidden">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary/15 ring-1 ring-primary/30">
            <Sparkles className="h-3.5 w-3.5 text-primary" />
          </div>
          <span className="text-sm font-semibold">celest.ia</span>
        </div>
        <Button
          variant="ghost"
          size="icon"
          aria-label={mobileOpen ? "Fechar menu" : "Abrir menu"}
          onClick={() => setMobileOpen((open) => !open)}
        >
          {mobileOpen ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </Button>
      </header>

      {mobileOpen && (
        <div className="fixed inset-0 z-30 lg:hidden">
          <div
            className="absolute inset-0 bg-black/60 backdrop-blur-sm"
            onClick={() => setMobileOpen(false)}
          />
          <aside className="absolute inset-y-0 left-0 flex w-72 flex-col border-r border-white/[0.06] bg-surface pt-14 animate-fade-in">
            <BrandMark />
            <NavLinks onNavigate={() => setMobileOpen(false)} />
            <RodapeUsuario onNavigate={() => setMobileOpen(false)} />
          </aside>
        </div>
      )}
    </>
  );
}
