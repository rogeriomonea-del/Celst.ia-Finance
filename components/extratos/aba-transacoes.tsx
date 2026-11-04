"use client";

/**
 * Aba "Transações": tabela paginada com ordenação clicável, edição de
 * categoria inline (PATCH auditado), divisão em partes (modal), seleção
 * múltipla para categorizar em lote e link de origem (documento original).
 *
 * Os filtros chegam prontos do pai (barra de filtros espelhada na URL);
 * aqui moram só paginação/ordenação. Valores em centavos int — divisão por
 * 100 acontece SÓ na formatação (`formatar-centavos.ts`).
 */

import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ChangeEvent,
} from "react";
import {
  ArrowDown,
  ArrowUp,
  ArrowUpDown,
  ChevronLeft,
  ChevronRight,
  Download,
  Plus,
  RefreshCw,
  Split,
  Trash2,
} from "lucide-react";
import type {
  CategoriaNo,
  Cartao,
  ColunaOrdenacao,
  Conta,
  DirecaoOrdenacao,
  FiltrosExtratos,
  PaginaTransacoes,
  ParteDivisao,
  Transacao,
} from "@/lib/types-extratos";
import { cn } from "@/lib/utils";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
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
  ErroApiExtratos,
  atualizarTransacao,
  dividirTransacao,
  listarCartoes,
  listarCategorias,
  listarContas,
  listarTransacoes,
  urlDownloadOriginal,
} from "@/components/extratos/api";
import {
  centavosParaTextoReais,
  formatarCentavos,
  formatarDataISO,
  formatarInteiro,
  parseReaisParaCentavos,
  textoOuTraco,
} from "@/components/extratos/formatar-centavos";
import { ROTULOS_CONCILIACAO, ROTULOS_TIPO } from "@/components/extratos/filtros";

const OPCOES_POR_PAGINA = [25, 50, 100, 200] as const;

const VARIANTE_CONCILIACAO: Record<
  string,
  "default" | "secondary" | "destructive" | "warning" | "muted" | "outline"
> = {
  conciliada: "default",
  pendente: "muted",
  divergente: "destructive",
  ignorada: "secondary",
};

interface OpcaoCategoria {
  valor: string;
  rotulo: string;
}

/** Achata a árvore de categorias com recuo por nível. */
function achatarCategorias(nos: CategoriaNo[], nivel = 0): OpcaoCategoria[] {
  const opcoes: OpcaoCategoria[] = [];
  for (const no of nos) {
    opcoes.push({ valor: String(no.id), rotulo: `${"— ".repeat(nivel)}${no.nome}` });
    if (no.filhas.length > 0) opcoes.push(...achatarCategorias(no.filhas, nivel + 1));
  }
  return opcoes;
}

function nomeDaCategoria(opcoes: OpcaoCategoria[], id: number): string | null {
  const opcao = opcoes.find((item) => item.valor === String(id));
  return opcao ? opcao.rotulo.replace(/^(?:— )+/, "") : null;
}

// --------------------------------------------------------------------------
// Modal de divisão
// --------------------------------------------------------------------------

interface ParteEmEdicao {
  categoriaId: string;
  valorTexto: string;
}

interface ModalDividirProps {
  transacao: Transacao;
  opcoesCategoria: OpcaoCategoria[];
  aberto: boolean;
  onFechar: () => void;
  onDividido: () => void;
}

function ModalDividir({
  transacao,
  opcoesCategoria,
  aberto,
  onFechar,
  onDividido,
}: ModalDividirProps) {
  const [partes, setPartes] = useState<ParteEmEdicao[]>([
    { categoriaId: "", valorTexto: centavosParaTextoReais(transacao.valor_centavos) },
    { categoriaId: "", valorTexto: "" },
  ]);
  const [salvando, setSalvando] = useState<boolean>(false);
  const [erro, setErro] = useState<string | null>(null);

  const somaCentavos = partes.reduce((soma, parte) => {
    const centavos = parseReaisParaCentavos(parte.valorTexto);
    return soma + (centavos !== null && centavos > 0 ? centavos : 0);
  }, 0);
  const diferenca = transacao.valor_centavos - somaCentavos;
  const partesValidas = partes.every((parte) => {
    const centavos = parseReaisParaCentavos(parte.valorTexto);
    return parte.categoriaId !== "" && centavos !== null && centavos > 0;
  });
  const podeSalvar = partesValidas && diferenca === 0 && partes.length >= 2;

  const confirmar = useCallback(async () => {
    setSalvando(true);
    setErro(null);
    const corpo: ParteDivisao[] = partes.map((parte) => ({
      categoria_id: Number.parseInt(parte.categoriaId, 10),
      valor_centavos: parseReaisParaCentavos(parte.valorTexto) ?? 0,
    }));
    try {
      await dividirTransacao(transacao.id, corpo);
      onDividido();
    } catch (falha: unknown) {
      setErro(
        falha instanceof ErroApiExtratos
          ? falha.message
          : "Não foi possível dividir a transação."
      );
      setSalvando(false);
    }
  }, [partes, transacao.id, onDividido]);

  return (
    <Dialog open={aberto} onOpenChange={(abrir) => !abrir && onFechar()}>
      <DialogContent aria-describedby="descricao-dividir">
        <DialogHeader>
          <DialogTitle>Dividir transação</DialogTitle>
          <DialogDescription id="descricao-dividir">
            {textoOuTraco(transacao.descricao_original)} ·{" "}
            {formatarCentavos(transacao.valor_centavos)} — a soma das partes
            precisa fechar exatamente com o valor da transação.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          {partes.map((parte, indice) => (
            <div key={indice} className="flex items-center gap-2">
              <select
                aria-label={`Categoria da parte ${indice + 1}`}
                value={parte.categoriaId}
                onChange={(evento: ChangeEvent<HTMLSelectElement>) =>
                  setPartes((atuais) =>
                    atuais.map((item, i) =>
                      i === indice ? { ...item, categoriaId: evento.target.value } : item
                    )
                  )
                }
                className="h-9 min-w-0 flex-1 rounded-xl border border-white/10 bg-surface-raised px-2.5 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
              >
                <option value="">Escolha a categoria…</option>
                {opcoesCategoria.map((opcao) => (
                  <option key={opcao.valor} value={opcao.valor}>
                    {opcao.rotulo}
                  </option>
                ))}
              </select>
              <Input
                inputMode="decimal"
                aria-label={`Valor da parte ${indice + 1} em reais`}
                placeholder="0,00"
                className="w-28 text-right tabular-nums"
                value={parte.valorTexto}
                onChange={(evento) =>
                  setPartes((atuais) =>
                    atuais.map((item, i) =>
                      i === indice ? { ...item, valorTexto: evento.target.value } : item
                    )
                  )
                }
              />
              <Button
                variant="ghost"
                size="icon"
                aria-label={`Remover parte ${indice + 1}`}
                disabled={partes.length <= 2}
                onClick={() =>
                  setPartes((atuais) => atuais.filter((_, i) => i !== indice))
                }
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            </div>
          ))}
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              setPartes((atuais) => [...atuais, { categoriaId: "", valorTexto: "" }])
            }
          >
            <Plus className="h-4 w-4" aria-hidden />
            Adicionar parte
          </Button>
        </div>

        <p
          aria-live="polite"
          className={cn(
            "text-sm tabular-nums",
            diferenca === 0 ? "text-profit" : "text-loss"
          )}
        >
          {diferenca === 0
            ? "Soma fechada com o valor da transação."
            : diferenca > 0
              ? `Faltam ${formatarCentavos(diferenca)} para fechar a soma.`
              : `A soma passa ${formatarCentavos(-diferenca)} do valor da transação.`}
        </p>
        {erro && (
          <p role="alert" className="text-sm text-loss">
            {erro}
          </p>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={onFechar} disabled={salvando}>
            Cancelar
          </Button>
          <Button onClick={() => void confirmar()} disabled={!podeSalvar || salvando}>
            {salvando ? "Dividindo…" : "Confirmar divisão"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// --------------------------------------------------------------------------
// Cabeçalho ordenável
// --------------------------------------------------------------------------

interface CabecalhoOrdenavelProps {
  rotulo: string;
  coluna: ColunaOrdenacao;
  ordenar: ColunaOrdenacao;
  dir: DirecaoOrdenacao;
  onOrdenar: (coluna: ColunaOrdenacao) => void;
  alinharDireita?: boolean;
}

function CabecalhoOrdenavel({
  rotulo,
  coluna,
  ordenar,
  dir,
  onOrdenar,
  alinharDireita = false,
}: CabecalhoOrdenavelProps) {
  const ativa = ordenar === coluna;
  const Icone = ativa ? (dir === "asc" ? ArrowUp : ArrowDown) : ArrowUpDown;
  return (
    <TableHead
      scope="col"
      aria-sort={ativa ? (dir === "asc" ? "ascending" : "descending") : "none"}
      className={cn(alinharDireita && "text-right")}
    >
      <button
        type="button"
        onClick={() => onOrdenar(coluna)}
        aria-label={`Ordenar por ${rotulo}`}
        className={cn(
          "inline-flex items-center gap-1 rounded-md uppercase tracking-wider transition-colors hover:text-foreground",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          ativa && "text-foreground"
        )}
      >
        {rotulo}
        <Icone className="h-3 w-3" aria-hidden />
      </button>
    </TableHead>
  );
}

// --------------------------------------------------------------------------
// Componente público
// --------------------------------------------------------------------------

export interface AbaTransacoesProps {
  /** Filtros ativos (a barra de filtros mora na página, espelhada na URL). */
  filtros: FiltrosExtratos;
}

export function AbaTransacoes({ filtros }: AbaTransacoesProps) {
  const [pagina, setPagina] = useState<number>(1);
  const [porPagina, setPorPagina] = useState<number>(50);
  const [ordenar, setOrdenar] = useState<ColunaOrdenacao>("data_operacao");
  const [dir, setDir] = useState<DirecaoOrdenacao>("desc");
  const [dados, setDados] = useState<PaginaTransacoes | null>(null);
  const [carregando, setCarregando] = useState<boolean>(true);
  const [erro, setErro] = useState<string | null>(null);
  const [tentativa, setTentativa] = useState<number>(0);

  const [categorias, setCategorias] = useState<CategoriaNo[]>([]);
  const [contas, setContas] = useState<Conta[]>([]);
  const [cartoes, setCartoes] = useState<Cartao[]>([]);

  const [selecionadas, setSelecionadas] = useState<ReadonlySet<number>>(new Set());
  const [categoriaLote, setCategoriaLote] = useState<string>("");
  const [aplicandoLote, setAplicandoLote] = useState<boolean>(false);
  const [salvandoId, setSalvandoId] = useState<number | null>(null);
  const [erroLinha, setErroLinha] = useState<string | null>(null);
  const [dividindo, setDividindo] = useState<Transacao | null>(null);

  // Filtros novos = volta para a página 1 e limpa a seleção.
  useEffect(() => {
    setPagina(1);
    setSelecionadas(new Set());
  }, [filtros]);

  // Listas de apoio (nomes de conta/cartão e categorias) — falha silenciosa.
  useEffect(() => {
    const controlador = new AbortController();
    void Promise.allSettled([
      listarCategorias(controlador.signal),
      listarContas(controlador.signal),
      listarCartoes(controlador.signal),
    ]).then(([resCategorias, resContas, resCartoes]) => {
      if (controlador.signal.aborted) return;
      if (resCategorias.status === "fulfilled" && Array.isArray(resCategorias.value)) {
        setCategorias(resCategorias.value);
      }
      if (resContas.status === "fulfilled" && Array.isArray(resContas.value)) {
        setContas(resContas.value);
      }
      if (resCartoes.status === "fulfilled" && Array.isArray(resCartoes.value)) {
        setCartoes(resCartoes.value);
      }
    });
    return () => controlador.abort();
  }, []);

  useEffect(() => {
    const controlador = new AbortController();
    setCarregando(true);
    setErro(null);
    listarTransacoes(
      filtros,
      { pagina, por_pagina: porPagina, ordenar, dir },
      controlador.signal
    )
      .then((resposta) => {
        setDados(resposta);
        setCarregando(false);
      })
      .catch((falha: unknown) => {
        if (controlador.signal.aborted) return;
        setErro(
          falha instanceof ErroApiExtratos
            ? falha.message
            : "Não foi possível carregar as transações."
        );
        setCarregando(false);
      });
    return () => controlador.abort();
  }, [filtros, pagina, porPagina, ordenar, dir, tentativa]);

  const recarregar = useCallback(() => setTentativa((n) => n + 1), []);

  const opcoesCategoria = useMemo(() => achatarCategorias(categorias), [categorias]);
  const nomesConta = useMemo(() => {
    const mapa = new Map<number, string>();
    for (const conta of contas) mapa.set(conta.id, conta.apelido || conta.nome);
    return mapa;
  }, [contas]);
  const nomesCartao = useMemo(() => {
    const mapa = new Map<number, string>();
    for (const cartao of cartoes) mapa.set(cartao.id, cartao.nome);
    return mapa;
  }, [cartoes]);

  const itens = dados?.itens ?? [];
  const total = dados?.total ?? 0;
  const totalPaginas = Math.max(1, Math.ceil(total / porPagina));

  const alternarOrdenacao = useCallback(
    (coluna: ColunaOrdenacao) => {
      if (ordenar === coluna) {
        setDir((atual) => (atual === "asc" ? "desc" : "asc"));
      } else {
        setOrdenar(coluna);
        setDir(coluna === "data_operacao" ? "desc" : "asc");
      }
      setPagina(1);
    },
    [ordenar]
  );

  const todasDaPaginaSelecionadas =
    itens.length > 0 && itens.every((item) => selecionadas.has(item.id));

  const alternarSelecaoPagina = useCallback(() => {
    setSelecionadas((atuais) => {
      const novo = new Set(atuais);
      if (itens.every((item) => novo.has(item.id))) {
        for (const item of itens) novo.delete(item.id);
      } else {
        for (const item of itens) novo.add(item.id);
      }
      return novo;
    });
  }, [itens]);

  const alternarSelecao = useCallback((id: number) => {
    setSelecionadas((atuais) => {
      const novo = new Set(atuais);
      if (novo.has(id)) novo.delete(id);
      else novo.add(id);
      return novo;
    });
  }, []);

  /** PATCH de categoria de UMA transação, com atualização local otimista-pós. */
  const mudarCategoria = useCallback(
    async (transacao: Transacao, categoriaTexto: string) => {
      const categoriaId =
        categoriaTexto === "" ? null : Number.parseInt(categoriaTexto, 10);
      setSalvandoId(transacao.id);
      setErroLinha(null);
      try {
        await atualizarTransacao(transacao.id, { categoria_id: categoriaId });
        setDados((atuais) =>
          atuais
            ? {
                ...atuais,
                itens: atuais.itens.map((item) =>
                  item.id === transacao.id
                    ? {
                        ...item,
                        categoria_id: categoriaId,
                        categoria_nome:
                          categoriaId === null
                            ? null
                            : nomeDaCategoria(opcoesCategoria, categoriaId),
                        origem_categoria: "manual",
                      }
                    : item
                ),
              }
            : atuais
        );
      } catch (falha: unknown) {
        setErroLinha(
          falha instanceof ErroApiExtratos
            ? falha.message
            : "Não foi possível atualizar a categoria."
        );
      } finally {
        setSalvandoId(null);
      }
    },
    [opcoesCategoria]
  );

  /** Categorização em lote: PATCH sequencial nas selecionadas e recarrega. */
  const aplicarCategoriaEmLote = useCallback(async () => {
    const categoriaId = Number.parseInt(categoriaLote, 10);
    if (!Number.isFinite(categoriaId)) return;
    setAplicandoLote(true);
    setErroLinha(null);
    let falhas = 0;
    for (const id of Array.from(selecionadas)) {
      try {
        await atualizarTransacao(id, { categoria_id: categoriaId });
      } catch {
        falhas += 1;
      }
    }
    setAplicandoLote(false);
    setSelecionadas(new Set());
    setCategoriaLote("");
    if (falhas > 0) {
      setErroLinha(
        `${falhas} de ${selecionadas.size} transações não puderam ser categorizadas.`
      );
    }
    recarregar();
  }, [categoriaLote, selecionadas, recarregar]);

  if (erro) {
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

  return (
    <div className="space-y-4">
      {selecionadas.size > 0 && (
        <div
          role="region"
          aria-label="Ações em lote"
          className="flex flex-wrap items-center gap-3 rounded-2xl border border-primary/20 bg-primary/5 p-3"
        >
          <span className="text-sm text-foreground" aria-live="polite">
            {formatarInteiro(selecionadas.size)} selecionada
            {selecionadas.size > 1 ? "s" : ""}
          </span>
          <select
            aria-label="Categoria para aplicar em lote"
            value={categoriaLote}
            onChange={(evento: ChangeEvent<HTMLSelectElement>) =>
              setCategoriaLote(evento.target.value)
            }
            className="h-9 rounded-xl border border-white/10 bg-surface-raised px-2.5 text-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            <option value="">Escolha a categoria…</option>
            {opcoesCategoria.map((opcao) => (
              <option key={opcao.valor} value={opcao.valor}>
                {opcao.rotulo}
              </option>
            ))}
          </select>
          <Button
            size="sm"
            disabled={categoriaLote === "" || aplicandoLote}
            onClick={() => void aplicarCategoriaEmLote()}
          >
            {aplicandoLote ? "Aplicando…" : "Aplicar à seleção"}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setSelecionadas(new Set())}
            disabled={aplicandoLote}
          >
            Limpar seleção
          </Button>
        </div>
      )}

      {erroLinha && (
        <p role="alert" className="rounded-xl border border-loss/30 bg-loss/5 p-3 text-sm text-loss">
          {erroLinha}
        </p>
      )}

      <Card>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead scope="col" className="w-10">
                    <input
                      type="checkbox"
                      role="checkbox"
                      aria-label="Selecionar todas as transações da página"
                      checked={todasDaPaginaSelecionadas}
                      onChange={alternarSelecaoPagina}
                      className="h-4 w-4 rounded border-white/20 bg-surface-raised accent-emerald-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    />
                  </TableHead>
                  <CabecalhoOrdenavel
                    rotulo="Data"
                    coluna="data_operacao"
                    ordenar={ordenar}
                    dir={dir}
                    onOrdenar={alternarOrdenacao}
                  />
                  <CabecalhoOrdenavel
                    rotulo="Descrição"
                    coluna="descricao_normalizada"
                    ordenar={ordenar}
                    dir={dir}
                    onOrdenar={alternarOrdenacao}
                  />
                  <TableHead scope="col">Contraparte</TableHead>
                  <TableHead scope="col">Conta / Cartão</TableHead>
                  <TableHead scope="col">Categoria</TableHead>
                  <CabecalhoOrdenavel
                    rotulo="Valor"
                    coluna="valor_centavos"
                    ordenar={ordenar}
                    dir={dir}
                    onOrdenar={alternarOrdenacao}
                    alinharDireita
                  />
                  <TableHead scope="col">Conciliação</TableHead>
                  <TableHead scope="col">Origem</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {carregando ? (
                  Array.from({ length: 8 }, (_, indice) => (
                    <TableRow key={`esqueleto-${indice}`}>
                      <TableCell colSpan={9}>
                        <Skeleton className="h-6 w-full" />
                      </TableCell>
                    </TableRow>
                  ))
                ) : itens.length === 0 ? (
                  <TableRow>
                    <TableCell
                      colSpan={9}
                      className="p-8 text-center text-sm text-muted-foreground"
                    >
                      Nenhuma transação com os filtros atuais. Ajuste os filtros
                      ou importe um extrato na aba Importar.
                    </TableCell>
                  </TableRow>
                ) : (
                  itens.map((transacao) => {
                    const ehCredito = transacao.direcao === "credito";
                    const origemConta =
                      transacao.conta_id !== null
                        ? (nomesConta.get(transacao.conta_id) ??
                          `Conta #${transacao.conta_id}`)
                        : transacao.cartao_id !== null
                          ? (nomesCartao.get(transacao.cartao_id) ??
                            `Cartão #${transacao.cartao_id}`)
                          : "—";
                    const tipoRotulo =
                      ROTULOS_TIPO[transacao.tipo as keyof typeof ROTULOS_TIPO] ??
                      transacao.tipo;
                    return (
                      <Fragment key={transacao.id}>
                        <TableRow
                          data-state={selecionadas.has(transacao.id) ? "selected" : undefined}
                        >
                          <TableCell>
                            <input
                              type="checkbox"
                              role="checkbox"
                              aria-label={`Selecionar transação ${textoOuTraco(transacao.descricao_original)}`}
                              checked={selecionadas.has(transacao.id)}
                              onChange={() => alternarSelecao(transacao.id)}
                              className="h-4 w-4 rounded border-white/20 bg-surface-raised accent-emerald-500 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                            />
                          </TableCell>
                          <TableCell className="whitespace-nowrap tabular-nums">
                            {formatarDataISO(transacao.data_operacao)}
                          </TableCell>
                          <TableCell className="max-w-[18rem]">
                            <span className="block truncate" title={transacao.descricao_original}>
                              {textoOuTraco(transacao.descricao_original)}
                            </span>
                            <span className="block text-[11px] text-muted-foreground">
                              {tipoRotulo}
                              {transacao.parcela_num !== null &&
                                transacao.parcela_total !== null &&
                                ` · parcela ${transacao.parcela_num}/${transacao.parcela_total}`}
                            </span>
                          </TableCell>
                          <TableCell className="max-w-[12rem]">
                            <span className="block truncate" title={transacao.contraparte ?? ""}>
                              {textoOuTraco(transacao.contraparte)}
                            </span>
                          </TableCell>
                          <TableCell className="whitespace-nowrap">{origemConta}</TableCell>
                          <TableCell>
                            <div className="flex items-center gap-1">
                              <select
                                aria-label={`Categoria da transação ${textoOuTraco(transacao.descricao_original)}`}
                                value={
                                  transacao.categoria_id !== null
                                    ? String(transacao.categoria_id)
                                    : ""
                                }
                                disabled={salvandoId === transacao.id}
                                onChange={(evento: ChangeEvent<HTMLSelectElement>) =>
                                  void mudarCategoria(transacao, evento.target.value)
                                }
                                className={cn(
                                  "h-8 max-w-[11rem] rounded-lg border border-white/10 bg-surface-raised px-2 text-xs",
                                  "focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring",
                                  transacao.categoria_id === null && "text-muted-foreground"
                                )}
                              >
                                <option value="">Sem categoria</option>
                                {opcoesCategoria.map((opcao) => (
                                  <option key={opcao.valor} value={opcao.valor}>
                                    {opcao.rotulo}
                                  </option>
                                ))}
                              </select>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-8 w-8"
                                aria-label={`Dividir transação ${textoOuTraco(transacao.descricao_original)} em partes`}
                                onClick={() => setDividindo(transacao)}
                              >
                                <Split className="h-3.5 w-3.5" />
                              </Button>
                            </div>
                          </TableCell>
                          <TableCell
                            className={cn(
                              "whitespace-nowrap text-right font-medium tabular-nums",
                              ehCredito ? "text-profit" : "text-loss"
                            )}
                          >
                            {ehCredito ? "+" : "−"}
                            {formatarCentavos(transacao.valor_centavos)}
                          </TableCell>
                          <TableCell>
                            <Badge
                              variant={
                                VARIANTE_CONCILIACAO[transacao.status_conciliacao] ?? "muted"
                              }
                            >
                              {ROTULOS_CONCILIACAO[
                                transacao.status_conciliacao as keyof typeof ROTULOS_CONCILIACAO
                              ] ?? transacao.status_conciliacao}
                            </Badge>
                          </TableCell>
                          <TableCell className="whitespace-nowrap">
                            {transacao.arquivo_id !== null ? (
                              <a
                                href={urlDownloadOriginal(transacao.arquivo_id)}
                                download={transacao.arquivo_nome ?? undefined}
                                aria-label={`Baixar documento original ${transacao.arquivo_nome ?? ""} (${transacao.origem_ref})`}
                                className="inline-flex items-center gap-1 text-xs text-primary underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                              >
                                <Download className="h-3 w-3" aria-hidden />
                                <span className="max-w-[9rem] truncate">
                                  {transacao.arquivo_nome ?? "original"}
                                </span>
                              </a>
                            ) : (
                              <span className="text-xs text-muted-foreground">—</span>
                            )}
                            {transacao.origem_ref && (
                              <span className="block text-[11px] text-muted-foreground">
                                {transacao.origem_ref}
                              </span>
                            )}
                          </TableCell>
                        </TableRow>
                      </Fragment>
                    );
                  })
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      <div className="flex flex-col items-center justify-between gap-3 sm:flex-row">
        <p className="text-sm text-muted-foreground" aria-live="polite">
          {carregando
            ? "Carregando transações…"
            : `${formatarInteiro(total)} transaç${total === 1 ? "ão" : "ões"} · página ${formatarInteiro(dados?.pagina ?? pagina)} de ${formatarInteiro(totalPaginas)}`}
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            Por página
            <select
              aria-label="Transações por página"
              value={String(porPagina)}
              onChange={(evento: ChangeEvent<HTMLSelectElement>) => {
                setPorPagina(Number.parseInt(evento.target.value, 10));
                setPagina(1);
              }}
              className="h-8 rounded-lg border border-white/10 bg-surface-raised px-2 text-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
            >
              {OPCOES_POR_PAGINA.map((opcao) => (
                <option key={opcao} value={String(opcao)}>
                  {opcao}
                </option>
              ))}
            </select>
          </label>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPagina((atual) => Math.max(1, atual - 1))}
            disabled={carregando || pagina <= 1}
            aria-label="Página anterior"
          >
            <ChevronLeft className="h-4 w-4" aria-hidden />
            Anterior
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => setPagina((atual) => Math.min(totalPaginas, atual + 1))}
            disabled={carregando || pagina >= totalPaginas}
            aria-label="Próxima página"
          >
            Próxima
            <ChevronRight className="h-4 w-4" aria-hidden />
          </Button>
        </div>
      </div>

      {dividindo && (
        <ModalDividir
          key={dividindo.id}
          transacao={dividindo}
          opcoesCategoria={opcoesCategoria}
          aberto
          onFechar={() => setDividindo(null)}
          onDividido={() => {
            setDividindo(null);
            recarregar();
          }}
        />
      )}
    </div>
  );
}
