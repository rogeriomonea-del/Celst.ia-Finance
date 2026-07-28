"use client";

import { useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  Bot,
  Briefcase,
  CalendarClock,
  FileUp,
  Gauge,
  Landmark,
  LayoutDashboard,
  Menu,
  Scale,
  ScrollText,
  Sparkles,
  UserRound,
  Wallet,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";

interface NavItem {
  href: string;
  label: string;
  description: string;
  icon: typeof LayoutDashboard;
  /** "api" = dados reais do backend; "simulado" = protótipo com dados estáticos. */
  tag?: "api" | "simulado";
}

const NAV_ITEMS: NavItem[] = [
  {
    href: "/visao-geral",
    label: "Visão geral",
    description: "Screener · Tesouro · Macro · Saúde",
    icon: Gauge,
    tag: "api",
  },
  {
    href: "/tesouro",
    label: "Tesouro Direto",
    description: "Curvas, radar e cenários MTM",
    icon: Landmark,
    tag: "api",
  },
  {
    href: "/macro",
    label: "Macro",
    description: "Regimes e séries do BCB",
    icon: Activity,
    tag: "api",
  },
  {
    href: "/politica",
    label: "Perfil e Política",
    description: "Questionário adaptativo · IPS",
    icon: ScrollText,
    tag: "api",
  },
  {
    href: "/importacao",
    label: "Importação B3",
    description: "Extrato → snapshot versionado",
    icon: FileUp,
    tag: "api",
  },
  {
    href: "/carteira",
    label: "Carteira",
    description: "Pesos, violações e qualidade",
    icon: Briefcase,
    tag: "api",
  },
  {
    href: "/plano-aportes",
    label: "Plano de aportes",
    description: "Rebalanceamento pela IPS",
    icon: CalendarClock,
    tag: "api",
  },
  {
    href: "/",
    label: "Dashboard",
    description: "Carteira e indicadores",
    icon: LayoutDashboard,
    tag: "simulado",
  },
  {
    href: "/agentes",
    label: "Agentes IA",
    description: "Análise multi-agente",
    icon: Bot,
    tag: "simulado",
  },
  {
    href: "/perfil",
    label: "Perfil",
    description: "Suitability do investidor",
    icon: UserRound,
    tag: "simulado",
  },
  {
    href: "/rebalanceamento",
    label: "Rebalanceamento",
    description: "Ajustes de alocação",
    icon: Scale,
    tag: "simulado",
  },
  {
    href: "/organizador",
    label: "Organizador",
    description: "Fluxo de caixa · Open Finance",
    icon: Wallet,
    tag: "simulado",
  },
];

function NavTag({ tag }: { tag: NonNullable<NavItem["tag"]> }) {
  return (
    <span
      className={cn(
        "ml-auto shrink-0 rounded-md px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wider",
        tag === "api"
          ? "bg-primary/15 text-primary ring-1 ring-primary/30"
          : "bg-amber-500/10 text-amber-400/90 ring-1 ring-amber-500/20"
      )}
      title={
        tag === "api"
          ? "Dados reais da API do backend Investment Intelligence OS"
          : "Protótipo com dados simulados — não tratar como dados reais"
      }
    >
      {tag === "api" ? "API" : "Simulado"}
    </span>
  );
}

function NavLinks({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();

  return (
    <nav className="flex flex-1 flex-col gap-1 px-3">
      {NAV_ITEMS.map((item) => {
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
            <span className="flex min-w-0 flex-col">
              <span className="font-medium leading-tight">{item.label}</span>
              <span className="truncate text-[11px] leading-tight text-muted-foreground/70">
                {item.description}
              </span>
            </span>
            {item.tag && <NavTag tag={item.tag} />}
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

export function Sidebar() {
  const [mobileOpen, setMobileOpen] = useState(false);

  return (
    <>
      <aside className="fixed inset-y-0 left-0 z-40 hidden w-64 flex-col border-r border-white/[0.06] bg-surface lg:flex">
        <BrandMark />
        <NavLinks />
        <div className="border-t border-white/[0.06] px-6 py-4">
          <p className="text-[11px] leading-relaxed text-muted-foreground/70">
            v2.0 Alpha · Módulos API: backend Investment OS
            <br />
            Módulos Simulado: protótipo (brapi.dev)
            <br />
            Uso educacional — não é recomendação.
          </p>
        </div>
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
          </aside>
        </div>
      )}
    </>
  );
}
