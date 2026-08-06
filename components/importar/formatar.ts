/**
 * Formatação pt-BR compartilhada pela aba "Importe suas Planilhas".
 *
 * O motor Python devolve percentuais como **fração** (0,12 = 12%) e datas como
 * texto ISO (`"2025-01-31"`), então tudo aqui converte antes de exibir.
 */

import type { FormatoValor } from "@/lib/types-planilha";
import {
  formatBRL,
  formatCompactBRL,
  formatNumber,
  formatPercentPlain,
} from "@/lib/utils";

const MESES_PT = [
  "jan",
  "fev",
  "mar",
  "abr",
  "mai",
  "jun",
  "jul",
  "ago",
  "set",
  "out",
  "nov",
  "dez",
] as const;

/** Converte qualquer entrada em número finito — nunca lança, nunca devolve NaN. */
export function numeroSeguro(valor: unknown): number {
  if (typeof valor === "number") return Number.isFinite(valor) ? valor : 0;
  if (typeof valor === "string") {
    const convertido = Number(valor.replace(",", "."));
    return Number.isFinite(convertido) ? convertido : 0;
  }
  return 0;
}

/** Fração para percentual: 0,12 vira "12,0%". */
export function formatarFracao(fracao: number, digitos = 1): string {
  return formatPercentPlain(numeroSeguro(fracao) * 100, digitos);
}

/** Fração com sinal explícito: 0,12 vira "+12,00%". */
export function formatarFracaoComSinal(fracao: number, digitos = 2): string {
  const percentual = numeroSeguro(fracao) * 100;
  const sinal = percentual >= 0 ? "+" : "";
  return `${sinal}${percentual.toFixed(digitos).replace(".", ",")}%`;
}

/** Dólar em pt-BR: "US$ 45.197,50". */
export function formatarUSD(valor: number): string {
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(numeroSeguro(valor));
}

/** Aplica o `value_format` que o motor sugeriu para o gráfico/KPI. */
export function formatarValor(valor: number, formato: FormatoValor | string): string {
  const numero = numeroSeguro(valor);
  if (formato === "percent") return formatarFracao(numero, 2);
  if (formato === "number") return formatNumber(numero, 2);
  if (formato === "text") return String(valor);
  return formatBRL(numero);
}

/** Versão compacta do mesmo valor, para os eixos dos gráficos. */
export function formatarEixo(valor: number, formato: FormatoValor | string): string {
  const numero = numeroSeguro(valor);
  if (formato === "percent") return formatarFracao(numero, 0);
  if (formato === "number") return formatNumber(numero, 0);
  if (formato === "text") return String(valor);
  return formatCompactBRL(numero);
}

/** Divide sem estourar: denominador zero devolve 0. */
export function dividirSeguro(numerador: number, denominador: number): number {
  const baixo = numeroSeguro(denominador);
  if (baixo === 0) return 0;
  const resultado = numeroSeguro(numerador) / baixo;
  return Number.isFinite(resultado) ? resultado : 0;
}

/** Quebra uma data ISO em partes, sem depender do fuso do navegador. */
function partesISO(iso: string | null | undefined): [number, number, number] | null {
  if (!iso || typeof iso !== "string") return null;
  const casado = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso.trim());
  if (!casado) return null;
  return [Number(casado[1]), Number(casado[2]), Number(casado[3])];
}

/** "2025-01-31" vira "31/01/2025"; entrada inválida vira "—". */
export function formatarDataISO(iso: string | null | undefined): string {
  const partes = partesISO(iso);
  if (!partes) return "—";
  const [ano, mes, dia] = partes;
  return `${String(dia).padStart(2, "0")}/${String(mes).padStart(2, "0")}/${ano}`;
}

/** "2025-01-31" vira "jan/2025"; entrada inválida volta como veio (ou "—"). */
export function formatarMesISO(iso: string | null | undefined): string {
  const partes = partesISO(iso);
  if (!partes) return iso ? String(iso) : "—";
  const [ano, mes] = partes;
  const nome = MESES_PT[Math.min(Math.max(mes, 1), 12) - 1];
  return `${nome}/${ano}`;
}

/** Texto vazio/ausente vira travessão, para a tabela não ficar com buracos. */
export function textoOuTraco(valor: string | null | undefined): string {
  const limpo = (valor ?? "").trim();
  return limpo === "" ? "—" : limpo;
}

/** Número opcional (`null` do Python) formatado, ou travessão. */
export function numeroOuTraco(
  valor: number | null | undefined,
  digitos = 2
): string {
  if (valor === null || valor === undefined || !Number.isFinite(valor)) return "—";
  return formatNumber(valor, digitos);
}

/** Fração opcional formatada, ou travessão. */
export function fracaoOuTraco(
  valor: number | null | undefined,
  digitos = 1
): string {
  if (valor === null || valor === undefined || !Number.isFinite(valor)) return "—";
  return formatarFracao(valor, digitos);
}
