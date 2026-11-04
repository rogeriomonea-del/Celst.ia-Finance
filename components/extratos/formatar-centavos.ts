/**
 * Formatação pt-BR do módulo de extratos.
 *
 * A API manda dinheiro como **centavos inteiros** (`1234_56` = R$ 1.234,56) e
 * datas como texto ISO. Tudo aqui converte só na EXIBIÇÃO — os valores seguem
 * inteiros até o último instante (a parte inteira e os centavos são separados
 * por aritmética de inteiros, sem passar o valor monetário por float).
 */

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

/** Agrupador de milhar pt-BR para inteiros (sem casas decimais). */
const INTEIRO_PT_BR = new Intl.NumberFormat("pt-BR", {
  maximumFractionDigits: 0,
});

/** Converte a entrada em inteiro de centavos — nunca lança, nunca NaN. */
export function centavosSeguro(valor: unknown): number {
  if (typeof valor === "number" && Number.isFinite(valor)) {
    return Math.trunc(valor);
  }
  if (typeof valor === "string") {
    const convertido = Number.parseInt(valor, 10);
    return Number.isFinite(convertido) ? convertido : 0;
  }
  return 0;
}

/** 123456 vira "R$ 1.234,56"; -50 vira "-R$ 0,50". Só inteiros no caminho. */
export function formatarCentavos(centavos: number, moeda = "R$"): string {
  const inteiro = centavosSeguro(centavos);
  const negativo = inteiro < 0;
  const absoluto = Math.abs(inteiro);
  const reais = Math.trunc(absoluto / 100);
  const resto = absoluto % 100;
  const texto = `${moeda} ${INTEIRO_PT_BR.format(reais)},${String(resto).padStart(2, "0")}`;
  return negativo ? `-${texto}` : texto;
}

/** Como `formatarCentavos`, mas com sinal explícito: "+R$ 10,00". */
export function formatarCentavosComSinal(centavos: number, moeda = "R$"): string {
  const inteiro = centavosSeguro(centavos);
  return inteiro >= 0
    ? `+${formatarCentavos(inteiro, moeda)}`
    : formatarCentavos(inteiro, moeda);
}

/** Versão compacta para eixos: "R$ 12,5 mil". Divisão só aqui, na exibição. */
export function formatarCentavosCompacto(centavos: number): string {
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
    notation: "compact",
    maximumFractionDigits: 1,
  }).format(centavosSeguro(centavos) / 100);
}

/** `null`/`undefined` vira travessão (valor desconhecido != zero). */
export function centavosOuTraco(
  centavos: number | null | undefined,
  moeda = "R$"
): string {
  if (centavos === null || centavos === undefined) return "—";
  return formatarCentavos(centavos, moeda);
}

/**
 * Percentual inteiro ×100, contrato do painel: 1350 vira "13,50%".
 * (Mesma convenção de inteiros do resto do módulo.)
 */
export function formatarPercentualX100(valorX100: number, digitos = 2): string {
  const inteiro = centavosSeguro(valorX100);
  const negativo = inteiro < 0;
  const absoluto = Math.abs(inteiro);
  const parteInteira = Math.trunc(absoluto / 100);
  const resto = String(absoluto % 100).padStart(2, "0");
  const decimais = digitos <= 0 ? "" : `,${resto.slice(0, digitos)}`;
  return `${negativo ? "-" : ""}${INTEIRO_PT_BR.format(parteInteira)}${decimais}%`;
}

/** Fração (0,12 = 12%) para percentual: "12,0%" — para números não monetários. */
export function formatarFracaoPercentual(fracao: number, digitos = 1): string {
  const numero = Number.isFinite(fracao) ? fracao : 0;
  return `${(numero * 100).toFixed(digitos).replace(".", ",")}%`;
}

/** Inteiro de contagem em pt-BR: 1234 vira "1.234". */
export function formatarInteiro(valor: number): string {
  return INTEIRO_PT_BR.format(centavosSeguro(valor));
}

/** Quebra uma data ISO em partes, sem depender do fuso do navegador. */
function partesISO(iso: string | null | undefined): [number, number, number] | null {
  if (!iso || typeof iso !== "string") return null;
  const casado = /^(\d{4})-(\d{2})(?:-(\d{2}))?/.exec(iso.trim());
  if (!casado) return null;
  return [Number(casado[1]), Number(casado[2]), Number(casado[3] ?? "1")];
}

/** "2026-08-07" vira "07/08/2026"; entrada inválida vira "—". */
export function formatarDataISO(iso: string | null | undefined): string {
  const partes = partesISO(iso);
  if (!partes) return "—";
  const [ano, mes, dia] = partes;
  return `${String(dia).padStart(2, "0")}/${String(mes).padStart(2, "0")}/${ano}`;
}

/** "2026-08" (ou "2026-08-07") vira "ago/2026"; inválida volta como veio. */
export function formatarMesISO(iso: string | null | undefined): string {
  const partes = partesISO(iso);
  if (!partes) return iso ? String(iso) : "—";
  const [ano, mes] = partes;
  const nome = MESES_PT[Math.min(Math.max(mes, 1), 12) - 1];
  return `${nome}/${ano}`;
}

/** "2026-08-07T13:40:00" vira "07/08/2026, 13:40" (ou o cru, se estranho). */
export function formatarDataHoraISO(iso: string | null | undefined): string {
  if (!iso) return "—";
  const data = new Date(iso);
  if (Number.isNaN(data.getTime())) return String(iso);
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
  }).format(data);
}

/** Texto vazio/ausente vira travessão, para a tabela não ficar com buracos. */
export function textoOuTraco(valor: string | null | undefined): string {
  const limpo = (valor ?? "").trim();
  return limpo === "" ? "—" : limpo;
}

/**
 * "1.234,56" (ou "1234,56", "1234.56", "1234") em centavos int — parse por
 * string, sem float. Entrada irreconhecível devolve `null`.
 */
export function parseReaisParaCentavos(texto: string): number | null {
  const limpo = texto.trim().replace(/^R\$\s*/i, "");
  if (limpo === "") return null;
  const casado = /^(-?)([\d.\s]*?)(?:[,.](\d{1,2}))?$/.exec(limpo.replace(/\s/g, ""));
  if (!casado) return null;
  const [, sinal, parteInteiraBruta, parteDecimal] = casado;
  const parteInteira = parteInteiraBruta.replace(/\./g, "");
  if (parteInteira !== "" && !/^\d+$/.test(parteInteira)) return null;
  if (parteInteira === "" && !parteDecimal) return null;
  const reais = parteInteira === "" ? 0 : Number.parseInt(parteInteira, 10);
  const centavos = parteDecimal ? Number.parseInt(parteDecimal.padEnd(2, "0"), 10) : 0;
  if (!Number.isFinite(reais) || !Number.isFinite(centavos)) return null;
  const total = reais * 100 + centavos;
  return sinal === "-" ? -total : total;
}

/** Centavos int para texto editável "1234,56" (sem símbolo de moeda). */
export function centavosParaTextoReais(centavos: number): string {
  const inteiro = centavosSeguro(centavos);
  const negativo = inteiro < 0;
  const absoluto = Math.abs(inteiro);
  const reais = Math.trunc(absoluto / 100);
  const resto = String(absoluto % 100).padStart(2, "0");
  return `${negativo ? "-" : ""}${reais},${resto}`;
}
