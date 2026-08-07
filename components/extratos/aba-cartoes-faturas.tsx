"use client";

/**
 * Aba "Cartões & Faturas": grade de cartões com edição inline (PATCH
 * auditado), tabela de faturas por cartão/competência com divergência
 * explícita (soma das compras ≠ valor declarado) e painel de conciliação —
 * sugestões automáticas com aceite em 1 clique, modo manual muitos-para-
 * -muitos (com valor parcial por fatura) e desfazer.
 *
 * Dinheiro chega em CENTAVOS int; divisão por 100 acontece só na exibição
 * (`formatar-centavos.ts`). O motor devolve listas embrulhadas em `{itens}`/
 * `{sugestoes}` — `extrairLista` aceita os dois formatos sem quebrar.
 */

import { useCallback, useEffect, useMemo, useState, type ChangeEvent } from "react";
import {
  CalendarClock,
  Check,
  CreditCard,
  Link2,
  Pencil,
  RefreshCw,
  Undo2,
  X,
} from "lucide-react";
import type {
  Cartao,
  Conta,
  Fatura,
  ItemConciliacao,
  Transacao,
} from "@/lib/types-extratos";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  BASE_EXTRATOS,
  ErroApiExtratos,
  criarConciliacao,
  listarCartoes,
  listarContas,
  listarFaturas,
  listarTransacoes,
} from "@/components/extratos/api";
import {
  centavosOuTraco,
  centavosParaTextoReais,
  centavosSeguro,
  formatarCentavos,
  formatarDataISO,
  formatarFracaoPercentual,
  formatarInteiro,
  formatarMesISO,
  parseReaisParaCentavos,
  textoOuTraco,
} from "@/components/extratos/formatar-centavos";

// --------------------------------------------------------------------------
// Chamadas locais (rotas que o helper compartilhado ainda não cobre)
// --------------------------------------------------------------------------

const MENSAGEM_SEM_MOTOR =
  "Não foi possível falar com o serviço de extratos. Verifique se o motor está no ar (python3 -m engine.server) e tente de novo.";

/** fetch tipado mínimo sobre a mesma base do cliente compartilhado. */
async function chamarExtratos<T>(
  metodo: "GET" | "POST" | "PATCH" | "DELETE",
  caminho: string,
  corpo?: unknown,
  signal?: AbortSignal
): Promise<T> {
  let resposta: Response;
  try {
    resposta = await fetch(`${BASE_EXTRATOS}${caminho}`, {
      method: metodo,
      signal,
      ...(corpo !== undefined
        ? {
            body: JSON.stringify(corpo),
            headers: { "Content-Type": "application/json" },
          }
        : {}),
    });
  } catch (falha) {
    if (falha instanceof DOMException && falha.name === "AbortError") throw falha;
    throw new ErroApiExtratos(MENSAGEM_SEM_MOTOR, 0);
  }
  const dados: unknown = await resposta.json().catch(() => null);
  if (!resposta.ok) {
    const mensagem =
      dados !== null &&
      typeof dados === "object" &&
      typeof (dados as { erro?: unknown }).erro === "string"
        ? String((dados as { erro: string }).erro)
        : `O motor de extratos recusou a operação (HTTP ${resposta.status}).`;
    throw new ErroApiExtratos(mensagem, resposta.status);
  }
  return dados as T;
}

/** Aceita resposta como lista crua OU embrulhada (`{itens}`, `{sugestoes}`…). */
function extrairLista<T>(bruto: unknown, chaves: readonly string[]): T[] {
  if (Array.isArray(bruto)) return bruto as T[];
  if (bruto !== null && typeof bruto === "object") {
    for (const chave of chaves) {
      const valor = (bruto as Record<string, unknown>)[chave];
      if (Array.isArray(valor)) return valor as T[];
    }
  }
  return [];
}

function mensagemDeFalha(falha: unknown, padrao: string): string {
  return falha instanceof ErroApiExtratos ? falha.message : padrao;
}

// --------------------------------------------------------------------------
// Tipos locais (contratos do motor ainda sem espelho em types-extratos)
// --------------------------------------------------------------------------

/** Uma fatura dentro de uma sugestão do motor de conciliação. */
interface FaturaSugerida {
  fatura_id: number;
  valor_sugerido_centavos: number;
}

/** Sugestão de `GET /extratos/conciliacoes/sugerir` (nada é gravado lá). */
interface SugestaoMotor {
  transacao_id: number;
  faturas?: FaturaSugerida[];
  /** Formatos achatados alternativos, tolerados por robustez. */
  fatura_id?: number;
  valor_centavos?: number;
  confianca?: number;
  motivo?: string;
}

/** Resposta de `POST /extratos/conciliacoes` (conciliacao.conciliar). */
interface RespostaConciliar {
  conciliacao_id?: number;
  status_conciliacao?: string;
  sobra_centavos?: number;
  avisos?: string[];
}

/** Conciliação criada NESTA sessão (a API ainda não expõe listagem). */
interface ConciliacaoDaSessao {
  id: number;
  quando: string;
  resumo: string;
  avisos: string[];
}

const ROTULOS_MOTIVO: Record<string, string> = {
  pagamento_integral: "Pagamento integral",
  pagamento_parcial: "Pagamento parcial",
  pagamento_agrupado: "Pagamento agrupado",
};

function traduzMotivo(motivo: string | undefined): string {
  if (!motivo) return "Sugestão do motor";
  const [prefixo, ...resto] = motivo.split(" ");
  const rotulo = ROTULOS_MOTIVO[prefixo];
  return rotulo ? `${rotulo} ${resto.join(" ")}`.trim() : motivo;
}

const ROTULOS_STATUS_FATURA: Record<string, string> = {
  aberta: "Aberta",
  fechada: "Fechada",
  vencida: "Vencida",
  paga: "Paga",
  parcial: "Parcial",
};

const VARIANTE_STATUS_FATURA: Record<
  string,
  "default" | "secondary" | "destructive" | "warning" | "muted" | "outline"
> = {
  aberta: "muted",
  fechada: "secondary",
  vencida: "destructive",
  paga: "default",
  parcial: "warning",
};

// --------------------------------------------------------------------------
// Cartão da grade (com edição inline)
// --------------------------------------------------------------------------

interface CamposCartaoEmEdicao {
  emissor: string;
  bandeira: string;
  final: string;
  diaFechamento: string;
  diaVencimento: string;
  limiteTexto: string;
  contaPagadoraId: string;
}

function camposDoCartao(cartao: Cartao): CamposCartaoEmEdicao {
  return {
    emissor: cartao.emissor ?? "",
    bandeira: cartao.bandeira ?? "",
    final: cartao.final ?? "",
    diaFechamento: cartao.dia_fechamento !== null ? String(cartao.dia_fechamento) : "",
    diaVencimento: cartao.dia_vencimento !== null ? String(cartao.dia_vencimento) : "",
    limiteTexto:
      cartao.limite_total_centavos !== null
        ? centavosParaTextoReais(cartao.limite_total_centavos)
        : "",
    contaPagadoraId:
      cartao.conta_pagadora_id !== null ? String(cartao.conta_pagadora_id) : "",
  };
}

interface CartaoDaGradeProps {
  cartao: Cartao;
  contas: Conta[];
  /** Centavos já utilizados (faturas não pagas), para a barra de limite. */
  usadoCentavos: number;
  onAtualizado: (cartao: Cartao) => void;
}

function CartaoDaGrade({ cartao, contas, usadoCentavos, onAtualizado }: CartaoDaGradeProps) {
  const [editando, setEditando] = useState<boolean>(false);
  const [campos, setCampos] = useState<CamposCartaoEmEdicao>(camposDoCartao(cartao));
  const [salvando, setSalvando] = useState<boolean>(false);
  const [erro, setErro] = useState<string | null>(null);

  const limite = cartao.limite_total_centavos;
  const temLimite = limite !== null && limite > 0;
  const percentualUso = temLimite
    ? Math.min(100, Math.max(0, Math.trunc((usadoCentavos * 100) / limite)))
    : 0;

  const mudar = (campo: keyof CamposCartaoEmEdicao) =>
    (evento: ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
      setCampos((atuais) => ({ ...atuais, [campo]: evento.target.value }));

  const salvar = useCallback(async () => {
    setErro(null);
    const corpo: Record<string, unknown> = {};

    const finalLimpo = campos.final.trim();
    if (finalLimpo !== "" && !/^\d{4}$/.test(finalLimpo)) {
      setErro("O final do cartão deve ter exatamente 4 dígitos (nunca o número completo).");
      return;
    }
    corpo.final = finalLimpo === "" ? null : finalLimpo;
    corpo.emissor = campos.emissor.trim() === "" ? null : campos.emissor.trim();
    corpo.bandeira = campos.bandeira.trim() === "" ? null : campos.bandeira.trim();

    for (const [texto, chave, rotulo] of [
      [campos.diaFechamento, "dia_fechamento", "dia de fechamento"],
      [campos.diaVencimento, "dia_vencimento", "dia de vencimento"],
    ] as const) {
      if (texto.trim() === "") {
        corpo[chave] = null;
        continue;
      }
      const dia = Number.parseInt(texto, 10);
      if (!Number.isFinite(dia) || dia < 1 || dia > 31) {
        setErro(`O ${rotulo} precisa ser um número entre 1 e 31.`);
        return;
      }
      corpo[chave] = dia;
    }

    if (campos.limiteTexto.trim() === "") {
      corpo.limite_total_centavos = null;
    } else {
      const limiteCentavos = parseReaisParaCentavos(campos.limiteTexto);
      if (limiteCentavos === null || limiteCentavos < 0) {
        setErro("Limite inválido — informe um valor em reais, como 12.000,00.");
        return;
      }
      corpo.limite_total_centavos = limiteCentavos;
    }

    corpo.conta_pagadora_id =
      campos.contaPagadoraId === "" ? null : Number.parseInt(campos.contaPagadoraId, 10);

    setSalvando(true);
    try {
      const atualizado = await chamarExtratos<Cartao>("PATCH", `/cartoes/${cartao.id}`, corpo);
      const contaNova = contas.find((conta) => conta.id === atualizado.conta_pagadora_id);
      onAtualizado({
        ...cartao,
        ...atualizado,
        conta_pagadora_nome: contaNova ? contaNova.apelido || contaNova.nome : null,
      });
      setEditando(false);
    } catch (falha: unknown) {
      setErro(mensagemDeFalha(falha, "Não foi possível salvar o cartão."));
    } finally {
      setSalvando(false);
    }
  }, [campos, cartao, contas, onAtualizado]);

  return (
    <Card>
      <CardContent className="space-y-3 p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="flex items-center gap-2 font-semibold">
              <CreditCard className="h-4 w-4 shrink-0 text-primary" aria-hidden />
              <span className="truncate">{cartao.nome}</span>
            </p>
            <p className="text-xs text-muted-foreground">
              {textoOuTraco(cartao.emissor)} · {textoOuTraco(cartao.bandeira)} ·{" "}
              <span className="tabular-nums">•••• {cartao.final ?? "————"}</span>
            </p>
          </div>
          {!editando && (
            <Button
              variant="ghost"
              size="icon"
              className="h-8 w-8"
              aria-label={`Editar cartão ${cartao.nome}`}
              onClick={() => {
                setCampos(camposDoCartao(cartao));
                setErro(null);
                setEditando(true);
              }}
            >
              <Pencil className="h-3.5 w-3.5" />
            </Button>
          )}
        </div>

        {editando ? (
          <div className="space-y-2">
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
              <label className="space-y-1 text-xs text-muted-foreground">
                Emissor
                <Input value={campos.emissor} onChange={mudar("emissor")} placeholder="Itaú, XP…" />
              </label>
              <label className="space-y-1 text-xs text-muted-foreground">
                Bandeira
                <Input value={campos.bandeira} onChange={mudar("bandeira")} placeholder="Visa, Mastercard…" />
              </label>
              <label className="space-y-1 text-xs text-muted-foreground">
                Final (só 4 dígitos)
                <Input
                  value={campos.final}
                  onChange={mudar("final")}
                  inputMode="numeric"
                  maxLength={4}
                  placeholder="1234"
                />
              </label>
              <label className="space-y-1 text-xs text-muted-foreground">
                Limite (R$)
                <Input
                  value={campos.limiteTexto}
                  onChange={mudar("limiteTexto")}
                  inputMode="decimal"
                  placeholder="12.000,00"
                  className="text-right tabular-nums"
                />
              </label>
              <label className="space-y-1 text-xs text-muted-foreground">
                Dia de fechamento
                <Input
                  value={campos.diaFechamento}
                  onChange={mudar("diaFechamento")}
                  inputMode="numeric"
                  placeholder="1–31"
                />
              </label>
              <label className="space-y-1 text-xs text-muted-foreground">
                Dia de vencimento
                <Input
                  value={campos.diaVencimento}
                  onChange={mudar("diaVencimento")}
                  inputMode="numeric"
                  placeholder="1–31"
                />
              </label>
            </div>
            <label className="block space-y-1 text-xs text-muted-foreground">
              Conta pagadora
              <select
                value={campos.contaPagadoraId}
                onChange={mudar("contaPagadoraId")}
                className="h-9 w-full rounded-xl border border-white/10 bg-surface-raised px-2.5 text-sm text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              >
                <option value="">Sem conta pagadora</option>
                {contas.map((conta) => (
                  <option key={conta.id} value={String(conta.id)}>
                    {conta.apelido || conta.nome}
                  </option>
                ))}
              </select>
            </label>
            {erro && (
              <p role="alert" className="text-xs text-loss">
                {erro}
              </p>
            )}
            <div className="flex items-center gap-2">
              <Button size="sm" onClick={() => void salvar()} disabled={salvando}>
                <Check className="h-3.5 w-3.5" aria-hidden />
                {salvando ? "Salvando…" : "Salvar"}
              </Button>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setEditando(false)}
                disabled={salvando}
              >
                <X className="h-3.5 w-3.5" aria-hidden />
                Cancelar
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-2 text-sm">
            <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
              <span>
                Fechamento:{" "}
                <span className="tabular-nums text-foreground">
                  {cartao.dia_fechamento !== null ? `dia ${cartao.dia_fechamento}` : "—"}
                </span>
              </span>
              <span>
                Vencimento:{" "}
                <span className="tabular-nums text-foreground">
                  {cartao.dia_vencimento !== null ? `dia ${cartao.dia_vencimento}` : "—"}
                </span>
              </span>
              <span>
                Conta pagadora:{" "}
                <span className="text-foreground">
                  {textoOuTraco(cartao.conta_pagadora_nome)}
                </span>
              </span>
            </div>
            {temLimite ? (
              <div className="space-y-1">
                <div className="flex items-center justify-between text-xs">
                  <span className="text-muted-foreground">Utilização do limite</span>
                  <span className="tabular-nums">
                    {formatarCentavos(usadoCentavos)} de {formatarCentavos(limite)} (
                    {percentualUso}%)
                  </span>
                </div>
                <Progress
                  value={percentualUso}
                  aria-label={`Utilização do limite do cartão ${cartao.nome}: ${percentualUso}%`}
                  indicatorClassName={cn(
                    percentualUso >= 90
                      ? "bg-loss"
                      : percentualUso >= 70
                        ? "bg-amber-500"
                        : "bg-primary"
                  )}
                />
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                Sem limite cadastrado — edite o cartão para informar.
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Componente público
// --------------------------------------------------------------------------

export function AbaCartoesFaturas() {
  const [cartoes, setCartoes] = useState<Cartao[]>([]);
  const [contas, setContas] = useState<Conta[]>([]);
  const [faturas, setFaturas] = useState<Fatura[]>([]);
  const [sugestoes, setSugestoes] = useState<SugestaoMotor[]>([]);
  const [pagamentos, setPagamentos] = useState<Transacao[]>([]);
  const [comprasPorFatura, setComprasPorFatura] = useState<ReadonlyMap<number, number>>(
    new Map()
  );

  const [carregando, setCarregando] = useState<boolean>(true);
  const [erro, setErro] = useState<string | null>(null);
  const [tentativa, setTentativa] = useState<number>(0);
  const recarregar = useCallback(() => setTentativa((n) => n + 1), []);

  const [filtroCartao, setFiltroCartao] = useState<string>("");
  const [filtroStatus, setFiltroStatus] = useState<string>("");

  const [aceitandoIndice, setAceitandoIndice] = useState<number | null>(null);
  const [erroConciliacao, setErroConciliacao] = useState<string | null>(null);
  const [conciliadas, setConciliadas] = useState<ConciliacaoDaSessao[]>([]);
  const [desfazendoId, setDesfazendoId] = useState<number | null>(null);

  const [selPagamentos, setSelPagamentos] = useState<ReadonlySet<number>>(new Set());
  const [selFaturas, setSelFaturas] = useState<ReadonlyMap<number, string>>(new Map());
  const [observacao, setObservacao] = useState<string>("");
  const [conciliandoManual, setConciliandoManual] = useState<boolean>(false);

  // Carga principal — cada bloco falha sozinho sem derrubar a aba inteira.
  useEffect(() => {
    const controlador = new AbortController();
    setCarregando(true);
    setErro(null);
    void Promise.allSettled([
      listarCartoes(controlador.signal),
      listarContas(controlador.signal),
      listarFaturas(null, controlador.signal),
      chamarExtratos<unknown>("GET", "/conciliacoes/sugerir", undefined, controlador.signal),
      listarTransacoes(
        { tipo: "pagamento_fatura", status_conciliacao: "pendente" },
        { por_pagina: 200, ordenar: "data_operacao", dir: "desc" },
        controlador.signal
      ),
    ]).then(([resCartoes, resContas, resFaturas, resSugestoes, resPagamentos]) => {
      if (controlador.signal.aborted) return;
      if (resCartoes.status === "fulfilled") {
        setCartoes(extrairLista<Cartao>(resCartoes.value, ["itens"]));
      } else {
        setErro(mensagemDeFalha(resCartoes.reason, "Não foi possível carregar os cartões."));
      }
      if (resContas.status === "fulfilled") {
        setContas(extrairLista<Conta>(resContas.value, ["itens"]));
      }
      if (resFaturas.status === "fulfilled") {
        setFaturas(extrairLista<Fatura>(resFaturas.value, ["itens"]));
      }
      if (resSugestoes.status === "fulfilled") {
        setSugestoes(extrairLista<SugestaoMotor>(resSugestoes.value, ["sugestoes", "itens"]));
      }
      if (resPagamentos.status === "fulfilled") {
        setPagamentos(extrairLista<Transacao>(resPagamentos.value, ["itens"]));
      }
      setCarregando(false);
    });
    return () => controlador.abort();
  }, [tentativa]);

  // Soma das compras por fatura (para a divergência), quando a API não manda
  // `compras_centavos`: usa os totais da própria listagem de transações.
  useEffect(() => {
    const pendentes = faturas
      .filter((fatura) => fatura.compras_centavos === undefined || fatura.compras_centavos === null)
      .slice(0, 60);
    if (pendentes.length === 0) return;
    const controlador = new AbortController();
    void Promise.allSettled(
      pendentes.map((fatura) =>
        listarTransacoes({ fatura_id: fatura.id }, { por_pagina: 1 }, controlador.signal)
      )
    ).then((resultados) => {
      if (controlador.signal.aborted) return;
      const mapa = new Map<number, number>();
      resultados.forEach((resultado, indice) => {
        if (resultado.status !== "fulfilled") return;
        const totais = (
          resultado.value as unknown as {
            totais?: { entradas_centavos?: number; saidas_centavos?: number };
          }
        ).totais;
        if (!totais) return;
        // Compras líquidas do ciclo = débitos − créditos (estornos abatem).
        mapa.set(
          pendentes[indice].id,
          centavosSeguro(totais.saidas_centavos) - centavosSeguro(totais.entradas_centavos)
        );
      });
      setComprasPorFatura(mapa);
    });
    return () => controlador.abort();
  }, [faturas]);

  const faturasPorId = useMemo(() => {
    const mapa = new Map<number, Fatura>();
    for (const fatura of faturas) mapa.set(fatura.id, fatura);
    return mapa;
  }, [faturas]);

  const pagamentosPorId = useMemo(() => {
    const mapa = new Map<number, Transacao>();
    for (const pagamento of pagamentos) mapa.set(pagamento.id, pagamento);
    return mapa;
  }, [pagamentos]);

  /** Compras conhecidas da fatura (API ou soma local); null = sem dado. */
  const comprasDaFatura = useCallback(
    (fatura: Fatura): number | null =>
      fatura.compras_centavos ?? comprasPorFatura.get(fatura.id) ?? null,
    [comprasPorFatura]
  );

  /** Quanto falta pagar da fatura (para o modo manual e a barra de limite). */
  const restanteDaFatura = useCallback(
    (fatura: Fatura): number => {
      const base = fatura.valor_centavos ?? comprasDaFatura(fatura) ?? 0;
      return Math.max(base - centavosSeguro(fatura.pago_centavos), 0);
    },
    [comprasDaFatura]
  );

  const usadoPorCartao = useMemo(() => {
    const mapa = new Map<number, number>();
    for (const fatura of faturas) {
      if (fatura.status === "paga") continue;
      const base = fatura.valor_centavos ?? comprasDaFatura(fatura) ?? 0;
      const restante = Math.max(base - centavosSeguro(fatura.pago_centavos), 0);
      mapa.set(fatura.cartao_id, (mapa.get(fatura.cartao_id) ?? 0) + restante);
    }
    return mapa;
  }, [faturas, comprasDaFatura]);

  const faturasVisiveis = useMemo(
    () =>
      faturas.filter(
        (fatura) =>
          (filtroCartao === "" || String(fatura.cartao_id) === filtroCartao) &&
          (filtroStatus === "" || fatura.status === filtroStatus)
      ),
    [faturas, filtroCartao, filtroStatus]
  );

  // ----- ações de conciliação -------------------------------------------

  const registrarConciliada = useCallback(
    (resposta: RespostaConciliar, resumo: string) => {
      if (typeof resposta.conciliacao_id !== "number") return;
      setConciliadas((atuais) => [
        {
          id: resposta.conciliacao_id as number,
          quando: new Date().toISOString(),
          resumo,
          avisos: Array.isArray(resposta.avisos) ? resposta.avisos : [],
        },
        ...atuais,
      ]);
    },
    []
  );

  const aceitarSugestao = useCallback(
    async (sugestao: SugestaoMotor, indice: number) => {
      const faturasDaSugestao: FaturaSugerida[] =
        sugestao.faturas ??
        (typeof sugestao.fatura_id === "number"
          ? [
              {
                fatura_id: sugestao.fatura_id,
                valor_sugerido_centavos: centavosSeguro(sugestao.valor_centavos),
              },
            ]
          : []);
      if (faturasDaSugestao.length === 0) return;
      const itens: ItemConciliacao[] = [
        { transacao_id: sugestao.transacao_id, papel: "pagamento" },
        ...faturasDaSugestao.map((item) => ({
          fatura_id: item.fatura_id,
          papel: "obrigacao" as const,
          valor_centavos: item.valor_sugerido_centavos,
        })),
      ];
      setAceitandoIndice(indice);
      setErroConciliacao(null);
      try {
        const resposta = (await criarConciliacao(
          itens,
          `aceite de sugestão: ${sugestao.motivo ?? "sem motivo"}`
        )) as RespostaConciliar;
        registrarConciliada(
          resposta,
          `Pagamento #${sugestao.transacao_id} ↔ fatura${faturasDaSugestao.length > 1 ? "s" : ""} ${faturasDaSugestao.map((f) => `#${f.fatura_id}`).join(", ")}`
        );
        recarregar();
      } catch (falha: unknown) {
        setErroConciliacao(mensagemDeFalha(falha, "Não foi possível aceitar a sugestão."));
      } finally {
        setAceitandoIndice(null);
      }
    },
    [registrarConciliada, recarregar]
  );

  const alternarPagamento = useCallback((id: number) => {
    setSelPagamentos((atuais) => {
      const novo = new Set(atuais);
      if (novo.has(id)) novo.delete(id);
      else novo.add(id);
      return novo;
    });
  }, []);

  const alternarFatura = useCallback(
    (fatura: Fatura) => {
      setSelFaturas((atuais) => {
        const novo = new Map(atuais);
        if (novo.has(fatura.id)) novo.delete(fatura.id);
        else novo.set(fatura.id, centavosParaTextoReais(restanteDaFatura(fatura)));
        return novo;
      });
    },
    [restanteDaFatura]
  );

  const mudarValorFatura = useCallback((faturaId: number, texto: string) => {
    setSelFaturas((atuais) => {
      const novo = new Map(atuais);
      novo.set(faturaId, texto);
      return novo;
    });
  }, []);

  const somaPagamentos = useMemo(
    () =>
      Array.from(selPagamentos).reduce(
        (soma, id) => soma + centavosSeguro(pagamentosPorId.get(id)?.valor_centavos),
        0
      ),
    [selPagamentos, pagamentosPorId]
  );

  const valoresFaturasValidos = useMemo(() => {
    const valores: Array<{ faturaId: number; centavos: number }> = [];
    let todosValidos = true;
    selFaturas.forEach((texto, faturaId) => {
      const centavos = parseReaisParaCentavos(texto);
      if (centavos === null || centavos <= 0) {
        todosValidos = false;
        return;
      }
      valores.push({ faturaId, centavos });
    });
    return { valores, todosValidos };
  }, [selFaturas]);

  const somaFaturas = useMemo(
    () => valoresFaturasValidos.valores.reduce((soma, item) => soma + item.centavos, 0),
    [valoresFaturasValidos]
  );

  const diferencaManual = somaPagamentos - somaFaturas;
  const podeConciliarManual =
    selPagamentos.size > 0 &&
    selFaturas.size > 0 &&
    valoresFaturasValidos.todosValidos &&
    valoresFaturasValidos.valores.length === selFaturas.size;

  const conciliarManual = useCallback(async () => {
    if (!podeConciliarManual) return;
    const itens: ItemConciliacao[] = [
      ...Array.from(selPagamentos).map((id) => ({
        transacao_id: id,
        papel: "pagamento" as const,
      })),
      ...valoresFaturasValidos.valores.map((item) => ({
        fatura_id: item.faturaId,
        papel: "obrigacao" as const,
        valor_centavos: item.centavos,
      })),
    ];
    setConciliandoManual(true);
    setErroConciliacao(null);
    try {
      const resposta = (await criarConciliacao(
        itens,
        observacao.trim() || "conciliação manual"
      )) as RespostaConciliar;
      registrarConciliada(
        resposta,
        `${selPagamentos.size} pagamento${selPagamentos.size > 1 ? "s" : ""} ↔ ${selFaturas.size} fatura${selFaturas.size > 1 ? "s" : ""} (manual)`
      );
      setSelPagamentos(new Set());
      setSelFaturas(new Map());
      setObservacao("");
      recarregar();
    } catch (falha: unknown) {
      setErroConciliacao(mensagemDeFalha(falha, "Não foi possível gravar a conciliação."));
    } finally {
      setConciliandoManual(false);
    }
  }, [
    podeConciliarManual,
    selPagamentos,
    selFaturas,
    valoresFaturasValidos,
    observacao,
    registrarConciliada,
    recarregar,
  ]);

  const desfazer = useCallback(
    async (conciliacaoId: number) => {
      setDesfazendoId(conciliacaoId);
      setErroConciliacao(null);
      try {
        await chamarExtratos<unknown>("DELETE", `/conciliacoes/${conciliacaoId}`);
        setConciliadas((atuais) => atuais.filter((item) => item.id !== conciliacaoId));
        recarregar();
      } catch (falha: unknown) {
        setErroConciliacao(mensagemDeFalha(falha, "Não foi possível desfazer a conciliação."));
      } finally {
        setDesfazendoId(null);
      }
    },
    [recarregar]
  );

  // ----- render ----------------------------------------------------------

  if (erro && cartoes.length === 0 && !carregando) {
    return (
      <Card role="alert">
        <CardContent className="flex flex-col items-center gap-3 p-8 text-center">
          <p className="text-sm text-muted-foreground">{erro}</p>
          <Button variant="outline" size="sm" onClick={recarregar}>
            <RefreshCw className="h-4 w-4" aria-hidden />
            Tentar de novo
          </Button>
        </CardContent>
      </Card>
    );
  }

  const faturasNaoPagas = faturas.filter((fatura) => fatura.status !== "paga");

  return (
    <div className="space-y-6">
      {/* ------------------------------------------------ grade de cartões */}
      <section aria-labelledby="titulo-cartoes" className="space-y-3">
        <div className="flex items-center justify-between gap-3">
          <h2 id="titulo-cartoes" className="text-lg font-semibold">
            Cartões
          </h2>
          <Button variant="ghost" size="sm" onClick={recarregar} aria-label="Recarregar dados">
            <RefreshCw className="h-4 w-4" aria-hidden />
            Recarregar
          </Button>
        </div>
        {carregando ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 3 }, (_, indice) => (
              <Skeleton key={indice} className="h-40 w-full rounded-2xl" />
            ))}
          </div>
        ) : cartoes.length === 0 ? (
          <Card>
            <CardContent className="p-8 text-center text-sm text-muted-foreground">
              Nenhum cartão cadastrado ainda. Importe a planilha de configuração
              na aba Config ou uma fatura na aba Importar.
            </CardContent>
          </Card>
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {cartoes.map((cartao) => (
              <CartaoDaGrade
                key={cartao.id}
                cartao={cartao}
                contas={contas}
                usadoCentavos={usadoPorCartao.get(cartao.id) ?? 0}
                onAtualizado={(atualizado) =>
                  setCartoes((atuais) =>
                    atuais.map((item) => (item.id === atualizado.id ? atualizado : item))
                  )
                }
              />
            ))}
          </div>
        )}
      </section>

      {/* ------------------------------------------------------- faturas */}
      <section aria-labelledby="titulo-faturas" className="space-y-3">
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
          <h2 id="titulo-faturas" className="text-lg font-semibold">
            Faturas por cartão e competência
          </h2>
          <div className="flex flex-wrap items-center gap-2">
            <select
              aria-label="Filtrar faturas por cartão"
              value={filtroCartao}
              onChange={(evento: ChangeEvent<HTMLSelectElement>) =>
                setFiltroCartao(evento.target.value)
              }
              className="h-9 rounded-xl border border-white/10 bg-surface-raised px-2.5 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              <option value="">Todos os cartões</option>
              {cartoes.map((cartao) => (
                <option key={cartao.id} value={String(cartao.id)}>
                  {cartao.nome}
                </option>
              ))}
            </select>
            <select
              aria-label="Filtrar faturas por status"
              value={filtroStatus}
              onChange={(evento: ChangeEvent<HTMLSelectElement>) =>
                setFiltroStatus(evento.target.value)
              }
              className="h-9 rounded-xl border border-white/10 bg-surface-raised px-2.5 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              <option value="">Todos os status</option>
              {Object.entries(ROTULOS_STATUS_FATURA).map(([valor, rotulo]) => (
                <option key={valor} value={valor}>
                  {rotulo}
                </option>
              ))}
            </select>
          </div>
        </div>
        <Card>
          <CardContent className="p-0">
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead scope="col">Cartão</TableHead>
                    <TableHead scope="col">Competência</TableHead>
                    <TableHead scope="col">Vencimento</TableHead>
                    <TableHead scope="col" className="text-right">
                      Valor
                    </TableHead>
                    <TableHead scope="col" className="text-right">
                      Pago
                    </TableHead>
                    <TableHead scope="col">Status</TableHead>
                    <TableHead scope="col">Divergência</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {carregando ? (
                    Array.from({ length: 5 }, (_, indice) => (
                      <TableRow key={`esqueleto-fatura-${indice}`}>
                        <TableCell colSpan={7}>
                          <Skeleton className="h-6 w-full" />
                        </TableCell>
                      </TableRow>
                    ))
                  ) : faturasVisiveis.length === 0 ? (
                    <TableRow>
                      <TableCell
                        colSpan={7}
                        className="p-8 text-center text-sm text-muted-foreground"
                      >
                        Nenhuma fatura com os filtros atuais.
                      </TableCell>
                    </TableRow>
                  ) : (
                    faturasVisiveis.map((fatura) => {
                      const compras = comprasDaFatura(fatura);
                      const temDivergencia =
                        compras !== null &&
                        compras > 0 &&
                        fatura.valor_centavos !== null &&
                        compras !== fatura.valor_centavos;
                      const diferenca = temDivergencia
                        ? compras - (fatura.valor_centavos ?? 0)
                        : 0;
                      return (
                        <TableRow key={fatura.id}>
                          <TableCell className="whitespace-nowrap">
                            {fatura.cartao_nome ??
                              cartoes.find((cartao) => cartao.id === fatura.cartao_id)?.nome ??
                              `Cartão #${fatura.cartao_id}`}
                          </TableCell>
                          <TableCell className="whitespace-nowrap tabular-nums">
                            {formatarMesISO(fatura.competencia)}
                          </TableCell>
                          <TableCell className="whitespace-nowrap tabular-nums">
                            {formatarDataISO(fatura.vence_em)}
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-right tabular-nums">
                            {centavosOuTraco(fatura.valor_centavos)}
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-right tabular-nums">
                            {formatarCentavos(fatura.pago_centavos)}
                          </TableCell>
                          <TableCell>
                            <Badge variant={VARIANTE_STATUS_FATURA[fatura.status] ?? "muted"}>
                              {ROTULOS_STATUS_FATURA[fatura.status] ?? fatura.status}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            {temDivergencia ? (
                              <span className="flex flex-col gap-0.5">
                                <Badge variant="warning" className="w-fit">
                                  Pendência
                                </Badge>
                                <span className="text-[11px] text-amber-400">
                                  Compras somam {formatarCentavos(compras)} —{" "}
                                  {diferenca > 0
                                    ? `${formatarCentavos(diferenca)} acima`
                                    : `${formatarCentavos(-diferenca)} abaixo`}{" "}
                                  do valor da fatura.
                                </span>
                              </span>
                            ) : (
                              <span className="text-xs text-muted-foreground">—</span>
                            )}
                          </TableCell>
                        </TableRow>
                      );
                    })
                  )}
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      </section>

      {/* ---------------------------------------------------- conciliação */}
      <section aria-labelledby="titulo-conciliacao" className="space-y-3">
        <h2 id="titulo-conciliacao" className="flex items-center gap-2 text-lg font-semibold">
          <Link2 className="h-5 w-5 text-primary" aria-hidden />
          Conciliação de pagamentos
        </h2>

        {erroConciliacao && (
          <p
            role="alert"
            className="rounded-xl border border-loss/30 bg-loss/5 p-3 text-sm text-loss"
          >
            {erroConciliacao}
          </p>
        )}

        {/* Sugestões automáticas */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Sugestões automáticas</CardTitle>
            <CardDescription>
              Pagamentos de fatura pendentes cruzados com faturas em aberto
              (valor aproximado, janela de vencimento e conta pagadora). Nada é
              gravado sem o seu aceite.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {carregando ? (
              <Skeleton className="h-16 w-full" />
            ) : sugestoes.length === 0 ? (
              <p className="text-sm text-muted-foreground" aria-live="polite">
                Nenhuma sugestão no momento — importe extratos com pagamentos de
                fatura ou use o modo manual abaixo.
              </p>
            ) : (
              <ul className="space-y-2">
                {sugestoes.map((sugestao, indice) => {
                  const pagamento = pagamentosPorId.get(sugestao.transacao_id);
                  const confianca = sugestao.confianca ?? 0;
                  const faturasDaSugestao =
                    sugestao.faturas ??
                    (typeof sugestao.fatura_id === "number"
                      ? [
                          {
                            fatura_id: sugestao.fatura_id,
                            valor_sugerido_centavos: centavosSeguro(sugestao.valor_centavos),
                          },
                        ]
                      : []);
                  return (
                    <li
                      key={`${sugestao.transacao_id}-${indice}`}
                      className="flex flex-col gap-3 rounded-xl border border-white/10 bg-surface-raised p-3 sm:flex-row sm:items-center sm:justify-between"
                    >
                      <div className="min-w-0 space-y-1">
                        <p className="text-sm">
                          Pagamento{" "}
                          <span className="tabular-nums">
                            {pagamento
                              ? `de ${formatarDataISO(pagamento.data_operacao)} · ${formatarCentavos(pagamento.valor_centavos)}`
                              : `#${sugestao.transacao_id}`}
                          </span>
                          {pagamento?.descricao_original && (
                            <span className="block truncate text-xs text-muted-foreground">
                              {pagamento.descricao_original}
                            </span>
                          )}
                        </p>
                        <p className="text-xs text-muted-foreground">
                          {faturasDaSugestao
                            .map((item) => {
                              const fatura = faturasPorId.get(item.fatura_id);
                              const nome = fatura
                                ? `${fatura.cartao_nome ?? `Cartão #${fatura.cartao_id}`} · ${formatarMesISO(fatura.competencia)}`
                                : `Fatura #${item.fatura_id}`;
                              return `${nome} (${formatarCentavos(item.valor_sugerido_centavos)})`;
                            })
                            .join(" + ")}
                        </p>
                        <p className="text-xs text-muted-foreground/80">
                          {traduzMotivo(sugestao.motivo)}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-2">
                        <Badge
                          variant={
                            confianca >= 0.85
                              ? "default"
                              : confianca >= 0.6
                                ? "warning"
                                : "muted"
                          }
                        >
                          Confiança {formatarFracaoPercentual(confianca, 0)}
                        </Badge>
                        <Button
                          size="sm"
                          disabled={aceitandoIndice !== null}
                          onClick={() => void aceitarSugestao(sugestao, indice)}
                        >
                          <Check className="h-3.5 w-3.5" aria-hidden />
                          {aceitandoIndice === indice ? "Aceitando…" : "Aceitar"}
                        </Button>
                      </div>
                    </li>
                  );
                })}
              </ul>
            )}
          </CardContent>
        </Card>

        {/* Modo manual muitos-para-muitos */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Conciliação manual (muitos-para-muitos)</CardTitle>
            <CardDescription>
              Selecione 1+ pagamentos e 1+ faturas. O valor por fatura é
              editável — informe menos que o restante para uma quitação parcial.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
              <div className="space-y-2">
                <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  Pagamentos pendentes ({formatarInteiro(pagamentos.length)})
                </p>
                <ul className="max-h-64 space-y-1 overflow-y-auto pr-1">
                  {pagamentos.length === 0 ? (
                    <li className="text-sm text-muted-foreground">
                      Nenhum pagamento de fatura pendente de conciliação.
                    </li>
                  ) : (
                    pagamentos.map((pagamento) => (
                      <li key={pagamento.id}>
                        <label className="flex cursor-pointer items-center gap-2 rounded-lg border border-white/5 bg-surface-raised px-2.5 py-1.5 text-sm hover:border-white/15">
                          <input
                            type="checkbox"
                            checked={selPagamentos.has(pagamento.id)}
                            onChange={() => alternarPagamento(pagamento.id)}
                            aria-label={`Selecionar pagamento de ${formatarDataISO(pagamento.data_operacao)} no valor de ${formatarCentavos(pagamento.valor_centavos)}`}
                            className="h-4 w-4 rounded border-white/20 bg-surface-raised accent-emerald-500"
                          />
                          <span className="tabular-nums">
                            {formatarDataISO(pagamento.data_operacao)}
                          </span>
                          <span className="min-w-0 flex-1 truncate text-muted-foreground">
                            {textoOuTraco(pagamento.descricao_original)}
                          </span>
                          <span className="tabular-nums font-medium">
                            {formatarCentavos(pagamento.valor_centavos)}
                          </span>
                        </label>
                      </li>
                    ))
                  )}
                </ul>
              </div>
              <div className="space-y-2">
                <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  Faturas em aberto ({formatarInteiro(faturasNaoPagas.length)})
                </p>
                <ul className="max-h-64 space-y-1 overflow-y-auto pr-1">
                  {faturasNaoPagas.length === 0 ? (
                    <li className="text-sm text-muted-foreground">
                      Nenhuma fatura em aberto.
                    </li>
                  ) : (
                    faturasNaoPagas.map((fatura) => {
                      const selecionada = selFaturas.has(fatura.id);
                      return (
                        <li key={fatura.id}>
                          <div
                            className={cn(
                              "flex items-center gap-2 rounded-lg border border-white/5 bg-surface-raised px-2.5 py-1.5 text-sm",
                              selecionada && "border-primary/40"
                            )}
                          >
                            <input
                              type="checkbox"
                              checked={selecionada}
                              onChange={() => alternarFatura(fatura)}
                              aria-label={`Selecionar fatura ${fatura.cartao_nome ?? `#${fatura.cartao_id}`} de ${formatarMesISO(fatura.competencia)}`}
                              className="h-4 w-4 rounded border-white/20 bg-surface-raised accent-emerald-500"
                            />
                            <span className="min-w-0 flex-1 truncate">
                              {fatura.cartao_nome ?? `Cartão #${fatura.cartao_id}`} ·{" "}
                              {formatarMesISO(fatura.competencia)}
                              <span className="block text-[11px] text-muted-foreground">
                                vence {formatarDataISO(fatura.vence_em)} · restante{" "}
                                {formatarCentavos(restanteDaFatura(fatura))}
                              </span>
                            </span>
                            {selecionada ? (
                              <Input
                                inputMode="decimal"
                                aria-label={`Valor a conciliar na fatura ${fatura.cartao_nome ?? fatura.cartao_id} de ${formatarMesISO(fatura.competencia)} (parcial permitido)`}
                                className="h-8 w-24 text-right text-xs tabular-nums"
                                value={selFaturas.get(fatura.id) ?? ""}
                                onChange={(evento) =>
                                  mudarValorFatura(fatura.id, evento.target.value)
                                }
                              />
                            ) : (
                              <span className="tabular-nums text-muted-foreground">
                                {centavosOuTraco(fatura.valor_centavos)}
                              </span>
                            )}
                          </div>
                        </li>
                      );
                    })
                  )}
                </ul>
              </div>
            </div>

            <div
              aria-live="polite"
              className="flex flex-col gap-2 rounded-xl border border-white/10 bg-surface-raised p-3 text-sm sm:flex-row sm:items-center sm:justify-between"
            >
              <span className="tabular-nums">
                Pagamentos: <strong>{formatarCentavos(somaPagamentos)}</strong> · Faturas:{" "}
                <strong>{formatarCentavos(somaFaturas)}</strong>
              </span>
              <span
                className={cn(
                  "tabular-nums",
                  diferencaManual === 0 ? "text-profit" : "text-amber-400"
                )}
              >
                {diferencaManual === 0
                  ? "Os dois lados fecham."
                  : diferencaManual > 0
                    ? `Sobra de ${formatarCentavos(diferencaManual)} nos pagamentos (será apontada como divergência).`
                    : `Faturas recebem ${formatarCentavos(-diferencaManual)} a mais que os pagamentos.`}
              </span>
            </div>
            {!valoresFaturasValidos.todosValidos && (
              <p role="alert" className="text-xs text-loss">
                Há fatura selecionada com valor inválido — informe um valor em
                reais maior que zero.
              </p>
            )}

            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <Input
                value={observacao}
                onChange={(evento) => setObservacao(evento.target.value)}
                placeholder="Observação (opcional, vai para a auditoria)"
                aria-label="Observação da conciliação manual"
                className="sm:max-w-md"
              />
              <Button
                disabled={!podeConciliarManual || conciliandoManual}
                onClick={() => void conciliarManual()}
              >
                <Link2 className="h-4 w-4" aria-hidden />
                {conciliandoManual ? "Conciliando…" : "Confirmar conciliação"}
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* Conciliações feitas nesta sessão */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <CalendarClock className="h-4 w-4 text-primary" aria-hidden />
              Conciliações desta sessão
            </CardTitle>
            <CardDescription>
              Desfazer estorna o pago das faturas, recalcula o status e devolve
              os pagamentos a pendente — tudo auditado. Conciliações de sessões
              anteriores aparecem pelo status das transações na aba Transações.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {conciliadas.length === 0 ? (
              <p className="text-sm text-muted-foreground" aria-live="polite">
                Nenhuma conciliação feita nesta sessão ainda.
              </p>
            ) : (
              <ul className="space-y-2">
                {conciliadas.map((item) => (
                  <li
                    key={item.id}
                    className="flex flex-col gap-2 rounded-xl border border-white/10 bg-surface-raised p-3 sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div className="min-w-0">
                      <p className="text-sm">
                        <span className="tabular-nums text-muted-foreground">
                          #{item.id} ·
                        </span>{" "}
                        {item.resumo}
                      </p>
                      {item.avisos.map((aviso, indice) => (
                        <p key={indice} className="text-xs text-amber-400">
                          {aviso}
                        </p>
                      ))}
                    </div>
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={desfazendoId !== null}
                      onClick={() => void desfazer(item.id)}
                      aria-label={`Desfazer conciliação ${item.id}`}
                    >
                      <Undo2 className="h-3.5 w-3.5" aria-hidden />
                      {desfazendoId === item.id ? "Desfazendo…" : "Desfazer"}
                    </Button>
                  </li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </section>
    </div>
  );
}
