"use client";

/** Avatar circular com foto (Google) ou iniciais, usado nas listas de contas. */

import { cn } from "@/lib/utils";
import { iniciaisDe } from "@/components/admin/formatar";

interface Props {
  nome: string | null;
  email: string;
  avatarUrl: string | null;
  className?: string;
}

export function AvatarConta({ nome, email, avatarUrl, className }: Props) {
  const classes = cn(
    "flex h-9 w-9 shrink-0 items-center justify-center overflow-hidden rounded-full border border-white/10 bg-surface-raised text-xs font-semibold text-foreground",
    className
  );
  if (avatarUrl) {
    return (
      // eslint-disable-next-line @next/next/no-img-element -- URL externa do provedor, sem otimização.
      <img src={avatarUrl} alt="" referrerPolicy="no-referrer" className={classes} />
    );
  }
  return (
    <span className={classes} aria-hidden>
      {iniciaisDe(nome, email)}
    </span>
  );
}
