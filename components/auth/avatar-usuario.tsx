"use client";

import { iniciaisDe, type Usuario } from "@/lib/auth/api";
import { cn } from "@/lib/utils";

interface AvatarUsuarioProps {
  usuario: Pick<Usuario, "nome" | "email" | "avatar_url">;
  className?: string;
}

/** Avatar do usuário: foto (Google) ou iniciais sobre fundo esmeralda. */
export function AvatarUsuario({ usuario, className }: AvatarUsuarioProps) {
  const rotulo = usuario.nome || usuario.email;
  if (usuario.avatar_url) {
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={usuario.avatar_url}
        alt={rotulo}
        referrerPolicy="no-referrer"
        className={cn(
          "h-9 w-9 shrink-0 rounded-full object-cover ring-1 ring-white/10",
          className
        )}
      />
    );
  }
  return (
    <div
      aria-label={rotulo}
      role="img"
      className={cn(
        "flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/15 text-xs font-semibold text-primary ring-1 ring-primary/30",
        className
      )}
    >
      {iniciaisDe(usuario)}
    </div>
  );
}
