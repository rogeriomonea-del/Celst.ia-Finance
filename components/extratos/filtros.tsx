"use client";

/**
 * Barra de filtros combináveis de extratos.
 *
 * Controlada pelo pai (`valor` + `onChange`), mas dona do espelhamento na URL:
 * cada mudança vira querystring via `router.replace` (preservando `?aba=` e o
 * resto), então filtros sobrevivem a refresh e viram links compartilháveis.
 * Busca e faixa de valor têm debounce de digitação; os selects de conta,
 * cartão e categoria são carregados da API (falha silenciosa: lista vazia —
 * o aviso de backend fora do ar é responsabilidade da página).
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type ReactNode,
} from "react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { FilterX, Search } from "lucide-react";
import type {
  Cartao,
  CategoriaNo,
  Conta,
  DirecaoTransacao,
  FiltrosExtratos,
  StatusCategoria,
  StatusConciliacao,
  TipoTransacao,
} from "@/lib/types-extratos";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  centavosParaTextoReais,
  parseReaisParaCentavos,
} from "@/components/extratos/formatar-centavos";
import { listarCartoes, listarCategorias, listarContas } from "@/components/extratos/api";

/** Atraso da digitação (busca e faixa de valor) antes de aplicar o filtro. */
const DEBOUNCE_MS = 400;

/** Chaves de filtro espelhadas na querystring (e limpas ao reescrever). */
const CHAVES_FILTRO = [
  "periodo_inicio",
  "periodo_fim",
  "busca",
  "instituicao_id",
  "conta_id",
  "cartao_id",
  "fatura_id",
  "categoria_id",
  "tipo",
  "direcao",
  "valor_min_centavos",
  "valor_max_centavos",
  "moeda",
  "status_conciliacao",
  "status_categoria",
  "lote_id",
  "arquivo_id",
] as const;

const CHAVES_INT = new Set<string>([
  "instituicao_id",
  "conta_id",
  "cartao_id",
  "fatura_id",
  "categoria_id",
  "valor_min_centavos",
  "valor_max_centavos",
  "lote_id",
  "arquivo_id",
]);

/** Rótulos pt-BR dos tipos de transação (vocabulário do motor). */
export const ROTULOS_TIPO: Record<TipoTransacao, string> = {
  receita: "Receita",
  despesa: "Despesa",
  transferencia: "Transferência",
  compra: "Compra (cartão)",
  estorno: "Estorno",
  tarifa: "Tarifa",
  juros: "Juros",
  pagamento_fatura: "Pagamento de fatura",
  ajuste: "Ajuste",
};

/** Rótulos pt-BR dos status de conciliação. */
export const ROTULOS_CONCILIACAO: Record<StatusConciliacao, string> = {
  pendente: "Pendente",
  conciliada: "Conciliada",
  divergente: "Divergente",
  ignorada: "Ignorada",
};

const ROTULOS_STATUS_CATEGORIA: Record<StatusCategoria, string> = {
  categorizada: "Categorizadas",
  nao_categorizada: "Sem categoria",
};

// --------------------------------------------------------------------------
// Filtros <-> querystring (helpers exportados: a página reusa no drill-down)
// --------------------------------------------------------------------------

/** Lê os filtros da querystring atual (chaves fora do vocabulário são ignoradas). */
export function filtrosDeSearchParams(params: URLSearchParams): FiltrosExtratos {
  const filtros: Record<string, string | number> = {};
  for (const chave of CHAVES_FILTRO) {
    const bruto = params.get(chave);
    if (bruto === null || bruto.trim() === "") continue;
    if (CHAVES_INT.has(chave)) {
      const numero = Number.parseInt(bruto, 10);
      if (Number.isFinite(numero)) filtros[chave] = numero;
    } else {
      filtros[chave] = bruto.trim();
    }
  }
  return filtros as FiltrosExtratos;
}

/** Reescreve as chaves de filtro em cima da querystring atual, preservando o resto. */
export function aplicarFiltrosEmParams(
  atuais: URLSearchParams,
  filtros: FiltrosExtratos
): URLSearchParams {
  const params = new URLSearchParams(atuais.toString());
  for (const chave of CHAVES_FILTRO) params.delete(chave);
  for (const chave of CHAVES_FILTRO) {
    const valor = filtros[chave];
    if (valor === null || valor === undefined) continue;
    const texto = String(valor).trim();
    if (texto === "") continue;
    params.set(chave, texto);
  }
  return params;
}

/** Quantos filtros estão ativos (para o contador do botão limpar). */
export function contarFiltrosAtivos(filtros: FiltrosExtratos): number {
  return CHAVES_FILTRO.reduce((total, chave) => {
    const valor = filtros[chave];
    return valor === null || valor === undefined || String(valor).trim() === ""
      ? total
      : total + 1;
  }, 0);
}

// --------------------------------------------------------------------------
// Peças internas
// --------------------------------------------------------------------------

interface OpcaoSelect {
  valor: string;
  rotulo: string;
}

interface SelectFiltroProps {
  rotulo: string;
  valor: string;
  opcoes: OpcaoSelect[];
  onChange: (valor: string) => void;
  rotuloTodos?: string;
}

/** Select nativo com o mesmo desenho do `<Input />` (dark premium). */
function SelectFiltro({
  rotulo,
  valor,
  opcoes,
  onChange,
  rotuloTodos = "Todos",
}: SelectFiltroProps) {
  return (
    <label className="flex min-w-0 flex-col gap-1">
      <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        {rotulo}
      </span>
      <select
        aria-label={rotulo}
        value={valor}
        onChange={(evento: ChangeEvent<HTMLSelectElement>) =>
          onChange(evento.target.value)
        }
        className={cn(
          "h-9 w-full rounded-xl border border-white/10 bg-surface-raised px-2.5 text-sm shadow-sm",
          "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
          valor === "" ? "text-muted-foreground" : "text-foreground"
        )}
      >
        <option value="">{rotuloTodos}</option>
        {opcoes.map((opcao) => (
          <option key={opcao.valor} value={opcao.valor}>
            {opcao.rotulo}
          </option>
        ))}
      </select>
    </label>
  );
}

function CampoRotulado({ rotulo, children }: { rotulo: string; children: ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-1">
      <span className="text-[11px] font-medium uppercase tracking-wider text-muted-foreground">
        {rotulo}
      </span>
      {children}
    </label>
  );
}

/** Achata a árvore de categorias com recuo por nível ("Moradia", "— Aluguel"). */
function achatarCategorias(nos: CategoriaNo[], nivel = 0): OpcaoSelect[] {
  const opcoes: OpcaoSelect[] = [];
  for (const no of nos) {
    opcoes.push({
      valor: String(no.id),
      rotulo: `${"— ".repeat(nivel)}${no.nome}`,
    });
    if (no.filhas.length > 0) {
      opcoes.push(...achatarCategorias(no.filhas, nivel + 1));
    }
  }
  return opcoes;
}

// --------------------------------------------------------------------------
// Componente público
// --------------------------------------------------------------------------

export interface BarraFiltrosProps {
  /** Filtros atuais (o pai é o dono do estado). */
  valor: FiltrosExtratos;
  /** Chamado a cada mudança, já com o objeto completo de filtros. */
  onChange: (filtros: FiltrosExtratos) => void;
  className?: string;
}

export function BarraFiltros({ valor, onChange, className }: BarraFiltrosProps) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const [contas, setContas] = useState<Conta[]>([]);
  const [cartoes, setCartoes] = useState<Cartao[]>([]);
  const [categorias, setCategorias] = useState<CategoriaNo[]>([]);

  // Campos com digitação: estado local + debounce antes de virar filtro.
  const [buscaTexto, setBuscaTexto] = useState<string>(valor.busca ?? "");
  const [valorMinTexto, setValorMinTexto] = useState<string>(
    valor.valor_min_centavos != null
      ? centavosParaTextoReais(valor.valor_min_centavos)
      : ""
  );
  const [valorMaxTexto, setValorMaxTexto] = useState<string>(
    valor.valor_max_centavos != null
      ? centavosParaTextoReais(valor.valor_max_centavos)
      : ""
  );
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Carrega as opções dos selects uma vez; falha vira lista vazia em silêncio.
  useEffect(() => {
    const controlador = new AbortController();
    void Promise.allSettled([
      listarContas(controlador.signal),
      listarCartoes(controlador.signal),
      listarCategorias(controlador.signal),
    ]).then(([resContas, resCartoes, resCategorias]) => {
      if (controlador.signal.aborted) return;
      if (resContas.status === "fulfilled" && Array.isArray(resContas.value)) {
        setContas(resContas.value);
      }
      if (resCartoes.status === "fulfilled" && Array.isArray(resCartoes.value)) {
        setCartoes(resCartoes.value);
      }
      if (
        resCategorias.status === "fulfilled" &&
        Array.isArray(resCategorias.value)
      ) {
        setCategorias(resCategorias.value);
      }
    });
    return () => controlador.abort();
  }, []);

  // Mudança vinda de fora (drill-down do painel, voltar do navegador):
  // sincroniza os campos de digitação sem disparar onChange de volta.
  useEffect(() => {
    setBuscaTexto(valor.busca ?? "");
  }, [valor.busca]);
  useEffect(() => {
    setValorMinTexto(
      valor.valor_min_centavos != null
        ? centavosParaTextoReais(valor.valor_min_centavos)
        : ""
    );
  }, [valor.valor_min_centavos]);
  useEffect(() => {
    setValorMaxTexto(
      valor.valor_max_centavos != null
        ? centavosParaTextoReais(valor.valor_max_centavos)
        : ""
    );
  }, [valor.valor_max_centavos]);

  /** Aplica a mudança: avisa o pai e espelha a querystring (replace, sem scroll). */
  const aplicar = useCallback(
    (parcial: Partial<FiltrosExtratos>) => {
      const combinado: FiltrosExtratos = { ...valor, ...parcial };
      // Normaliza: vazio/nulo sai do objeto para a querystring ficar limpa.
      const novo: FiltrosExtratos = {};
      for (const chave of CHAVES_FILTRO) {
        const bruto = combinado[chave];
        if (bruto === null || bruto === undefined) continue;
        if (String(bruto).trim() === "") continue;
        (novo as Record<string, unknown>)[chave] = bruto;
      }
      onChange(novo);
      const params = aplicarFiltrosEmParams(searchParams, novo);
      const query = params.toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [valor, onChange, searchParams, router, pathname]
  );

  /** Digitação com debounce: busca e faixa de valor aplicam depois da pausa. */
  const aplicarComDebounce = useCallback(
    (parcial: () => Partial<FiltrosExtratos>) => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
      debounceRef.current = setTimeout(() => aplicar(parcial()), DEBOUNCE_MS);
    },
    [aplicar]
  );
  useEffect(
    () => () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    },
    []
  );

  const opcoesConta = useMemo<OpcaoSelect[]>(
    () =>
      contas.map((conta) => ({
        valor: String(conta.id),
        rotulo: conta.apelido || conta.nome,
      })),
    [contas]
  );
  const opcoesCartao = useMemo<OpcaoSelect[]>(
    () =>
      cartoes.map((cartao) => ({
        valor: String(cartao.id),
        rotulo: cartao.final ? `${cartao.nome} · final ${cartao.final}` : cartao.nome,
      })),
    [cartoes]
  );
  const opcoesCategoria = useMemo<OpcaoSelect[]>(
    () => achatarCategorias(categorias),
    [categorias]
  );
  const opcoesTipo = useMemo<OpcaoSelect[]>(
    () =>
      (Object.entries(ROTULOS_TIPO) as Array<[TipoTransacao, string]>).map(
        ([tipo, rotulo]) => ({ valor: tipo, rotulo })
      ),
    []
  );
  const opcoesConciliacao = useMemo<OpcaoSelect[]>(
    () =>
      (
        Object.entries(ROTULOS_CONCILIACAO) as Array<[StatusConciliacao, string]>
      ).map(([status, rotulo]) => ({ valor: status, rotulo })),
    []
  );
  const opcoesStatusCategoria = useMemo<OpcaoSelect[]>(
    () =>
      (
        Object.entries(ROTULOS_STATUS_CATEGORIA) as Array<[StatusCategoria, string]>
      ).map(([status, rotulo]) => ({ valor: status, rotulo })),
    []
  );

  const ativos = contarFiltrosAtivos(valor);

  const paraInt = (texto: string): number | null => {
    if (texto === "") return null;
    const numero = Number.parseInt(texto, 10);
    return Number.isFinite(numero) ? numero : null;
  };

  return (
    <section
      aria-label="Filtros de transações"
      className={cn(
        "space-y-3 rounded-2xl border border-white/[0.06] bg-surface p-4",
        className
      )}
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <CampoRotulado rotulo="Buscar">
          <div className="relative">
            <Search
              className="pointer-events-none absolute left-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden
            />
            <Input
              type="search"
              inputMode="search"
              aria-label="Buscar por descrição ou contraparte"
              placeholder="Descrição ou contraparte…"
              className="pl-8"
              value={buscaTexto}
              onChange={(evento) => {
                const texto = evento.target.value;
                setBuscaTexto(texto);
                aplicarComDebounce(() => ({ busca: texto.trim() || null }));
              }}
            />
          </div>
        </CampoRotulado>

        <CampoRotulado rotulo="De">
          <Input
            type="date"
            aria-label="Início do período"
            value={valor.periodo_inicio ?? ""}
            onChange={(evento) =>
              aplicar({ periodo_inicio: evento.target.value || null })
            }
          />
        </CampoRotulado>

        <CampoRotulado rotulo="Até">
          <Input
            type="date"
            aria-label="Fim do período"
            value={valor.periodo_fim ?? ""}
            onChange={(evento) =>
              aplicar({ periodo_fim: evento.target.value || null })
            }
          />
        </CampoRotulado>

        <div className="grid grid-cols-2 gap-2">
          <CampoRotulado rotulo="Valor mín. (R$)">
            <Input
              inputMode="decimal"
              aria-label="Valor mínimo em reais"
              placeholder="0,00"
              value={valorMinTexto}
              onChange={(evento) => {
                const texto = evento.target.value;
                setValorMinTexto(texto);
                aplicarComDebounce(() => ({
                  valor_min_centavos:
                    texto.trim() === "" ? null : parseReaisParaCentavos(texto),
                }));
              }}
            />
          </CampoRotulado>
          <CampoRotulado rotulo="Valor máx. (R$)">
            <Input
              inputMode="decimal"
              aria-label="Valor máximo em reais"
              placeholder="9.999,99"
              value={valorMaxTexto}
              onChange={(evento) => {
                const texto = evento.target.value;
                setValorMaxTexto(texto);
                aplicarComDebounce(() => ({
                  valor_max_centavos:
                    texto.trim() === "" ? null : parseReaisParaCentavos(texto),
                }));
              }}
            />
          </CampoRotulado>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-6">
        <SelectFiltro
          rotulo="Conta"
          rotuloTodos="Todas"
          valor={valor.conta_id != null ? String(valor.conta_id) : ""}
          opcoes={opcoesConta}
          onChange={(texto) => aplicar({ conta_id: paraInt(texto) })}
        />
        <SelectFiltro
          rotulo="Cartão"
          rotuloTodos="Todos"
          valor={valor.cartao_id != null ? String(valor.cartao_id) : ""}
          opcoes={opcoesCartao}
          onChange={(texto) => aplicar({ cartao_id: paraInt(texto) })}
        />
        <SelectFiltro
          rotulo="Categoria"
          rotuloTodos="Todas"
          valor={valor.categoria_id != null ? String(valor.categoria_id) : ""}
          opcoes={opcoesCategoria}
          onChange={(texto) => aplicar({ categoria_id: paraInt(texto) })}
        />
        <SelectFiltro
          rotulo="Tipo"
          valor={valor.tipo ?? ""}
          opcoes={opcoesTipo}
          onChange={(texto) =>
            aplicar({ tipo: texto === "" ? null : (texto as TipoTransacao) })
          }
        />
        <SelectFiltro
          rotulo="Conciliação"
          rotuloTodos="Qualquer"
          valor={valor.status_conciliacao ?? ""}
          opcoes={opcoesConciliacao}
          onChange={(texto) =>
            aplicar({
              status_conciliacao:
                texto === "" ? null : (texto as StatusConciliacao),
            })
          }
        />
        <SelectFiltro
          rotulo="Categorização"
          rotuloTodos="Qualquer"
          valor={valor.status_categoria ?? ""}
          opcoes={opcoesStatusCategoria}
          onChange={(texto) =>
            aplicar({
              status_categoria: texto === "" ? null : (texto as StatusCategoria),
            })
          }
        />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <SelectDirecao
            valor={valor.direcao ?? ""}
            onChange={(texto) =>
              aplicar({
                direcao: texto === "" ? null : (texto as DirecaoTransacao),
              })
            }
          />
          <span aria-live="polite">
            {ativos === 0
              ? "Nenhum filtro ativo"
              : `${ativos} filtro${ativos > 1 ? "s" : ""} ativo${ativos > 1 ? "s" : ""}`}
          </span>
        </div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => aplicar(Object.fromEntries(CHAVES_FILTRO.map((c) => [c, null])))}
          disabled={ativos === 0}
          aria-label="Limpar todos os filtros"
        >
          <FilterX className="h-4 w-4" aria-hidden />
          Limpar filtros
        </Button>
      </div>
    </section>
  );
}

/** Alternância entrada/saída como pílulas (crédito esmeralda, débito carmim). */
function SelectDirecao({
  valor,
  onChange,
}: {
  valor: string;
  onChange: (valor: string) => void;
}) {
  const opcoes: Array<{ valor: string; rotulo: string; classe: string }> = [
    { valor: "", rotulo: "Tudo", classe: "" },
    { valor: "credito", rotulo: "Entradas", classe: "data-[ativo=true]:text-profit" },
    { valor: "debito", rotulo: "Saídas", classe: "data-[ativo=true]:text-loss" },
  ];
  return (
    <div
      role="group"
      aria-label="Direção da transação"
      className="inline-flex h-8 items-center rounded-xl bg-surface-raised p-1"
    >
      {opcoes.map((opcao) => (
        <button
          key={opcao.valor || "tudo"}
          type="button"
          data-ativo={valor === opcao.valor}
          aria-pressed={valor === opcao.valor}
          onClick={() => onChange(opcao.valor)}
          className={cn(
            "rounded-lg px-2.5 py-0.5 text-xs font-medium transition-colors",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            valor === opcao.valor
              ? "bg-white/10 text-foreground shadow"
              : "text-muted-foreground hover:text-foreground",
            opcao.classe
          )}
        >
          {opcao.rotulo}
        </button>
      ))}
    </div>
  );
}
