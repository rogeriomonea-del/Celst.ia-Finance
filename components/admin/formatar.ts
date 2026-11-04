/**
 * Formatação pt-BR da área de administração.
 *
 * O motor manda datas como texto `YYYY-MM-DD HH:MM:SS` em **UTC** (mesmo
 * formato do `datetime('now')` do SQLite). Aqui isso vira data/hora local do
 * navegador via `Intl.DateTimeFormat` — só na exibição.
 */

const DATA_HORA_PT_BR = new Intl.DateTimeFormat("pt-BR", {
  day: "2-digit",
  month: "2-digit",
  year: "numeric",
  hour: "2-digit",
  minute: "2-digit",
});

const RELATIVO_PT_BR = new Intl.RelativeTimeFormat("pt-BR", { numeric: "auto" });

/** Interpreta a data UTC do motor; `null` quando vazia ou irreconhecível. */
export function interpretarDataUtc(texto: string | null | undefined): Date | null {
  if (!texto) return null;
  const limpo = texto.trim();
  if (!limpo) return null;
  // "2025-01-31 12:34:56" → "2025-01-31T12:34:56Z"; ISO já com fuso fica como está.
  const iso = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}/.test(limpo)
    ? `${limpo.replace(" ", "T")}Z`
    : /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?(\.\d+)?$/.test(limpo)
      ? `${limpo}Z`
      : limpo;
  const data = new Date(iso);
  return Number.isNaN(data.getTime()) ? null : data;
}

/** "31/01/2025 09:34" no fuso local; travessão quando não há data. */
export function formatarDataUtc(texto: string | null | undefined): string {
  const data = interpretarDataUtc(texto);
  return data ? DATA_HORA_PT_BR.format(data) : "—";
}

/** "há 5 minutos", "ontem", "há 3 dias"… para a última atividade. */
export function formatarRelativo(texto: string | null | undefined, agora = Date.now()): string {
  const data = interpretarDataUtc(texto);
  if (!data) return "—";
  const segundos = Math.round((data.getTime() - agora) / 1000);
  const absoluto = Math.abs(segundos);
  if (absoluto < 60) return RELATIVO_PT_BR.format(segundos, "second");
  if (absoluto < 3600) return RELATIVO_PT_BR.format(Math.round(segundos / 60), "minute");
  if (absoluto < 86400) return RELATIVO_PT_BR.format(Math.round(segundos / 3600), "hour");
  if (absoluto < 86400 * 30) return RELATIVO_PT_BR.format(Math.round(segundos / 86400), "day");
  return RELATIVO_PT_BR.format(Math.round(segundos / (86400 * 30)), "month");
}

/**
 * Resume um user-agent em "Navegador · Sistema" (ex.: "Chrome · Windows").
 * Nunca lança; sem pistas devolve o texto original truncado.
 */
export function resumirUserAgent(userAgent: string | null | undefined): string {
  const ua = (userAgent ?? "").trim();
  if (!ua) return "Dispositivo desconhecido";

  const navegador = ua.includes("Edg/")
    ? "Edge"
    : ua.includes("OPR/") || ua.includes("Opera")
      ? "Opera"
      : ua.includes("SamsungBrowser")
        ? "Samsung Internet"
        : ua.includes("Firefox/")
          ? "Firefox"
          : ua.includes("Chrome/") || ua.includes("CriOS/")
            ? "Chrome"
            : ua.includes("Safari/") && !ua.includes("Chrome")
              ? "Safari"
              : ua.toLowerCase().includes("curl") || ua.toLowerCase().includes("python")
                ? "Cliente de linha de comando"
                : null;

  const sistema = ua.includes("iPhone") || ua.includes("iPad")
    ? "iOS"
    : ua.includes("Android")
      ? "Android"
      : ua.includes("Windows")
        ? "Windows"
        : ua.includes("Mac OS X") || ua.includes("Macintosh")
          ? "macOS"
          : ua.includes("CrOS")
            ? "ChromeOS"
            : ua.includes("Linux")
              ? "Linux"
              : null;

  if (!navegador && !sistema) return ua.length > 48 ? `${ua.slice(0, 45)}…` : ua;
  return [navegador, sistema].filter(Boolean).join(" · ");
}

/** Iniciais para o avatar: "Ana Souza" → "AS"; sem nome usa a 1ª letra do e-mail. */
export function iniciaisDe(nome: string | null | undefined, email: string): string {
  const base = (nome ?? "").trim();
  if (base) {
    const partes = base.split(/\s+/).filter(Boolean);
    const primeira = partes[0]?.[0] ?? "";
    const ultima = partes.length > 1 ? partes[partes.length - 1]?.[0] ?? "" : "";
    return `${primeira}${ultima}`.toUpperCase();
  }
  return (email[0] ?? "?").toUpperCase();
}

/** Rótulos pt-BR das ações registradas em `eventos_acesso`. */
const ROTULOS_ACAO: Record<string, string> = {
  conta_criada: "Conta criada",
  conta_aguardando_aprovacao: "Conta aguardando aprovação",
  login_codigo: "Login por código",
  login_google: "Login com Google",
  codigo_enviado: "Código enviado",
  codigo_solicitado: "Código solicitado",
  codigo_invalido: "Código inválido",
  codigo_incorreto: "Código incorreto",
  codigo_expirado: "Código expirado",
  codigo_bloqueado: "Código bloqueado",
  conta_inativa: "Conta inativa",
  saiu: "Saiu",
  saiu_de_tudo: "Saiu de todos os dispositivos",
  sessoes_encerradas: "Sessões encerradas",
  admin_alterou_conta: "Admin alterou conta",
  admin_encerrou_sessao: "Admin encerrou sessão",
  admin_encerrou_sessoes: "Admin encerrou sessões",
};

/** "login_codigo" → "Login por código"; ação desconhecida vira texto legível. */
export function rotuloAcao(acao: string): string {
  if (ROTULOS_ACAO[acao]) return ROTULOS_ACAO[acao];
  const legivel = acao.replace(/_/g, " ").trim();
  return legivel ? legivel[0].toUpperCase() + legivel.slice(1) : "—";
}
