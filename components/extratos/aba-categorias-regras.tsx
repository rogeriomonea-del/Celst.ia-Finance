"use client";

/**
 * Aba "Categorias & Regras": árvore de categorias (expandir/recolher, uso,
 * criar/renomear subcategoria), CRUD de regras de categorização, aplicação de
 * regras em duas etapas (dry-run com prévia → confirmação), fila de
 * duplicidades com resolução auditada e histórico de auditoria da transação
 * selecionada (quando a API expuser `GET /extratos/transacoes/{id}`).
 *
 * Dinheiro em CENTAVOS int; conversão para reais só na exibição
 * (`formatar-centavos.ts`). Listas do motor chegam embrulhadas (`{arvore}`,
 * `{itens}`) — `extrairLista` tolera os dois formatos.
 */

import { useCallback, useEffect, useMemo, useState, type ChangeEvent } from "react";
import {
  Check,
  ChevronDown,
  ChevronRight,
  Copy,
  FolderTree,
  History,
  Pencil,
  Plus,
  RefreshCw,
  Wand2,
  X,
} from "lucide-react";
import type {
  Cartao,
  CategoriaNo,
  Conta,
  Duplicidade,
  RegraCategoria,
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
  BASE_EXTRATOS,
  ErroApiExtratos,
  listarCartoes,
  listarCategorias,
  listarContas,
  listarDuplicidades,
  listarRegras,
  listarTransacoes,
} from "@/components/extratos/api";
import {
  centavosOuTraco,
  centavosParaTextoReais,
  formatarCentavos,
  formatarDataHoraISO,
  formatarDataISO,
  formatarFracaoPercentual,
  formatarInteiro,
  parseReaisParaCentavos,
  textoOuTraco,
} from "@/components/extratos/formatar-centavos";

// --------------------------------------------------------------------------
// Chamadas locais (rotas ainda sem espelho no cliente compartilhado)
// --------------------------------------------------------------------------

const MENSAGEM_SEM_MOTOR =
  "Não foi possível falar com o serviço de extratos. Verifique se o motor está no ar (python3 -m engine.server) e tente de novo.";

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
// Tipos locais (contratos do motor)
// --------------------------------------------------------------------------

/** Item da prévia de `POST /extratos/regras/aplicar` (dry-run). */
interface ItemPreviaRegra {
  transacao_id: number;
  categoria_atual: number | null;
  categoria_sugerida: number;
  regra_id: number;
  confianca?: number;
}

interface RespostaAplicarRegras {
  dry_run?: boolean;
  previa?: ItemPreviaRegra[];
  aplicadas?: number;
  avisos?: string[];
  erro?: string;
}

/** Linha de `GET /extratos/duplicidades` (junta campos da transação). */
interface LinhaDuplicidade extends Duplicidade {
  data_operacao?: string | null;
  valor_centavos?: number | null;
  moeda?: string | null;
  descricao_original?: string | null;
  conta_id?: number | null;
  cartao_id?: number | null;
  [extra: string]: unknown;
}

/** Evento de auditoria (campo `auditoria` de GET /extratos/transacoes/{id}). */
interface EventoAuditoria {
  quando?: string;
  acao?: string;
  campo?: string | null;
  valor_anterior?: string | null;
  valor_novo?: string | null;
  origem?: string;
  justificativa?: string | null;
}

const ROTULOS_CAMPO_REGRA: Record<string, string> = {
  descricao: "Descrição",
  contraparte: "Contraparte",
};

const ROTULOS_OPERADOR: Record<string, string> = {
  contem: "contém",
  igual: "é igual a",
  regex: "casa com a regex",
  comeca: "começa com",
};

// --------------------------------------------------------------------------
// Árvore de categorias
// --------------------------------------------------------------------------

function achatarCategorias(
  nos: CategoriaNo[],
  nivel = 0
): Array<{ id: number; rotulo: string }> {
  const opcoes: Array<{ id: number; rotulo: string }> = [];
  for (const no of nos) {
    opcoes.push({ id: no.id, rotulo: `${"— ".repeat(nivel)}${no.nome}` });
    if (no.filhas.length > 0) opcoes.push(...achatarCategorias(no.filhas, nivel + 1));
  }
  return opcoes;
}

interface NoArvoreProps {
  no: CategoriaNo;
  nivel: number;
  onRenomear: (id: number, nome: string) => Promise<void>;
  onCriarFilha: (paiId: number | null, nome: string) => Promise<void>;
}

function NoArvore({ no, nivel, onRenomear, onCriarFilha }: NoArvoreProps) {
  const [expandido, setExpandido] = useState<boolean>(nivel === 0);
  const [renomeando, setRenomeando] = useState<boolean>(false);
  const [nomeNovo, setNomeNovo] = useState<string>(no.nome);
  const [criandoFilha, setCriandoFilha] = useState<boolean>(false);
  const [nomeFilha, setNomeFilha] = useState<string>("");
  const [ocupado, setOcupado] = useState<boolean>(false);

  const confirmarRenomear = async () => {
    if (nomeNovo.trim() === "" || nomeNovo.trim() === no.nome) {
      setRenomeando(false);
      return;
    }
    setOcupado(true);
    await onRenomear(no.id, nomeNovo.trim());
    setOcupado(false);
    setRenomeando(false);
  };

  const confirmarFilha = async () => {
    if (nomeFilha.trim() === "") return;
    setOcupado(true);
    await onCriarFilha(no.id, nomeFilha.trim());
    setOcupado(false);
    setNomeFilha("");
    setCriandoFilha(false);
    setExpandido(true);
  };

  return (
    <li>
      <div
        className="group flex flex-wrap items-center gap-1.5 rounded-lg px-1.5 py-1 hover:bg-white/5"
        style={{ paddingLeft: `${nivel * 1.25 + 0.375}rem` }}
      >
        {no.filhas.length > 0 ? (
          <button
            type="button"
            onClick={() => setExpandido((atual) => !atual)}
            aria-expanded={expandido}
            aria-label={`${expandido ? "Recolher" : "Expandir"} categoria ${no.nome}`}
            className="rounded p-0.5 text-muted-foreground hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {expandido ? (
              <ChevronDown className="h-3.5 w-3.5" aria-hidden />
            ) : (
              <ChevronRight className="h-3.5 w-3.5" aria-hidden />
            )}
          </button>
        ) : (
          <span className="w-[1.125rem]" aria-hidden />
        )}

        {renomeando ? (
          <span className="flex items-center gap-1.5">
            <Input
              value={nomeNovo}
              onChange={(evento) => setNomeNovo(evento.target.value)}
              aria-label={`Novo nome para a categoria ${no.nome}`}
              className="h-7 w-40 text-sm"
            />
            <Button
              size="icon"
              variant="ghost"
              className="h-7 w-7"
              disabled={ocupado}
              aria-label="Confirmar renomeação"
              onClick={() => void confirmarRenomear()}
            >
              <Check className="h-3.5 w-3.5" />
            </Button>
            <Button
              size="icon"
              variant="ghost"
              className="h-7 w-7"
              disabled={ocupado}
              aria-label="Cancelar renomeação"
              onClick={() => {
                setRenomeando(false);
                setNomeNovo(no.nome);
              }}
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          </span>
        ) : (
          <>
            <span className="text-sm">{no.nome}</span>
            <Badge variant="muted" aria-label={`${no.uso} usos`}>
              {formatarInteiro(no.uso)}
            </Badge>
            <span className="flex items-center gap-0.5 opacity-0 transition-opacity focus-within:opacity-100 group-hover:opacity-100">
              <Button
                size="icon"
                variant="ghost"
                className="h-7 w-7"
                aria-label={`Renomear categoria ${no.nome}`}
                onClick={() => {
                  setNomeNovo(no.nome);
                  setRenomeando(true);
                }}
              >
                <Pencil className="h-3 w-3" />
              </Button>
              <Button
                size="icon"
                variant="ghost"
                className="h-7 w-7"
                aria-label={`Criar subcategoria em ${no.nome}`}
                onClick={() => setCriandoFilha((atual) => !atual)}
              >
                <Plus className="h-3 w-3" />
              </Button>
            </span>
          </>
        )}
      </div>

      {criandoFilha && (
        <div
          className="flex items-center gap-1.5 py-1"
          style={{ paddingLeft: `${(nivel + 1) * 1.25 + 0.375}rem` }}
        >
          <Input
            value={nomeFilha}
            onChange={(evento) => setNomeFilha(evento.target.value)}
            placeholder="Nome da subcategoria"
            aria-label={`Nome da nova subcategoria de ${no.nome}`}
            className="h-7 w-44 text-sm"
          />
          <Button
            size="sm"
            variant="outline"
            className="h-7"
            disabled={ocupado || nomeFilha.trim() === ""}
            onClick={() => void confirmarFilha()}
          >
            Criar
          </Button>
        </div>
      )}

      {expandido && no.filhas.length > 0 && (
        <ul>
          {no.filhas.map((filha) => (
            <NoArvore
              key={filha.id}
              no={filha}
              nivel={nivel + 1}
              onRenomear={onRenomear}
              onCriarFilha={onCriarFilha}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

// --------------------------------------------------------------------------
// Formulário de regra (criar/editar)
// --------------------------------------------------------------------------

interface CamposRegra {
  prioridade: string;
  campo: string;
  operador: string;
  valor: string;
  contaId: string;
  cartaoId: string;
  valorMinTexto: string;
  valorMaxTexto: string;
  categoriaId: string;
  ativa: boolean;
}

function camposDaRegra(regra: RegraCategoria | null): CamposRegra {
  return {
    prioridade: regra ? String(regra.prioridade) : "100",
    campo: regra?.campo ?? "descricao",
    operador: regra?.operador ?? "contem",
    valor: regra?.valor ?? "",
    contaId: regra?.conta_id !== null && regra !== null ? String(regra.conta_id) : "",
    cartaoId: regra?.cartao_id !== null && regra !== null ? String(regra.cartao_id) : "",
    valorMinTexto:
      regra?.valor_min_centavos !== null && regra !== null && regra.valor_min_centavos !== undefined
        ? centavosParaTextoReais(regra.valor_min_centavos)
        : "",
    valorMaxTexto:
      regra?.valor_max_centavos !== null && regra !== null && regra.valor_max_centavos !== undefined
        ? centavosParaTextoReais(regra.valor_max_centavos)
        : "",
    categoriaId: regra ? String(regra.categoria_id) : "",
    ativa: regra ? regra.ativo === 1 : true,
  };
}

interface FormularioRegraProps {
  regra: RegraCategoria | null;
  opcoesCategoria: Array<{ id: number; rotulo: string }>;
  contas: Conta[];
  cartoes: Cartao[];
  aberto: boolean;
  onFechar: () => void;
  onSalvo: () => void;
}

function FormularioRegra({
  regra,
  opcoesCategoria,
  contas,
  cartoes,
  aberto,
  onFechar,
  onSalvo,
}: FormularioRegraProps) {
  const [campos, setCampos] = useState<CamposRegra>(camposDaRegra(regra));
  const [salvando, setSalvando] = useState<boolean>(false);
  const [erro, setErro] = useState<string | null>(null);

  const mudar = (campo: keyof CamposRegra) =>
    (evento: ChangeEvent<HTMLInputElement | HTMLSelectElement>) =>
      setCampos((atuais) => ({ ...atuais, [campo]: evento.target.value }));

  const salvar = useCallback(async () => {
    setErro(null);
    if (campos.valor.trim() === "") {
      setErro("O valor da regra é obrigatório (texto a comparar).");
      return;
    }
    if (campos.categoriaId === "") {
      setErro("Escolha a categoria de destino.");
      return;
    }
    const prioridade = Number.parseInt(campos.prioridade, 10);
    if (!Number.isFinite(prioridade)) {
      setErro("Prioridade inválida — informe um número inteiro (menor aplica antes).");
      return;
    }
    let valorMin: number | null = null;
    let valorMax: number | null = null;
    if (campos.valorMinTexto.trim() !== "") {
      valorMin = parseReaisParaCentavos(campos.valorMinTexto);
      if (valorMin === null) {
        setErro("Valor mínimo inválido — use reais, como 10,00.");
        return;
      }
    }
    if (campos.valorMaxTexto.trim() !== "") {
      valorMax = parseReaisParaCentavos(campos.valorMaxTexto);
      if (valorMax === null) {
        setErro("Valor máximo inválido — use reais, como 500,00.");
        return;
      }
    }
    const corpo = {
      prioridade,
      ativo: campos.ativa ? 1 : 0,
      campo: campos.campo,
      operador: campos.operador,
      valor: campos.valor,
      conta_id: campos.contaId === "" ? null : Number.parseInt(campos.contaId, 10),
      cartao_id: campos.cartaoId === "" ? null : Number.parseInt(campos.cartaoId, 10),
      valor_min_centavos: valorMin,
      valor_max_centavos: valorMax,
      categoria_id: Number.parseInt(campos.categoriaId, 10),
    };
    setSalvando(true);
    try {
      if (regra) {
        await chamarExtratos<unknown>("PATCH", `/regras/${regra.id}`, corpo);
      } else {
        await chamarExtratos<unknown>("POST", "/regras", corpo);
      }
      onSalvo();
    } catch (falha: unknown) {
      setErro(mensagemDeFalha(falha, "Não foi possível salvar a regra."));
      setSalvando(false);
    }
  }, [campos, regra, onSalvo]);

  const classeSelect =
    "h-9 w-full rounded-xl border border-white/10 bg-surface-raised px-2.5 text-sm text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring";

  return (
    <Dialog open={aberto} onOpenChange={(abrir) => !abrir && onFechar()}>
      <DialogContent aria-describedby="descricao-regra" className="max-h-[85vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{regra ? `Editar regra #${regra.id}` : "Nova regra"}</DialogTitle>
          <DialogDescription id="descricao-regra">
            A primeira regra que casa vence (prioridade menor aplica antes).
            Categoria definida manualmente nunca é sobrescrita por regra.
          </DialogDescription>
        </DialogHeader>

        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="space-y-1 text-xs text-muted-foreground">
            Prioridade
            <Input
              value={campos.prioridade}
              onChange={mudar("prioridade")}
              inputMode="numeric"
              className="tabular-nums"
            />
          </label>
          <label className="space-y-1 text-xs text-muted-foreground">
            Campo comparado
            <select value={campos.campo} onChange={mudar("campo")} className={classeSelect}>
              {Object.entries(ROTULOS_CAMPO_REGRA).map(([valor, rotulo]) => (
                <option key={valor} value={valor}>
                  {rotulo}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1 text-xs text-muted-foreground">
            Operador
            <select value={campos.operador} onChange={mudar("operador")} className={classeSelect}>
              {Object.entries(ROTULOS_OPERADOR).map(([valor, rotulo]) => (
                <option key={valor} value={valor}>
                  {rotulo}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1 text-xs text-muted-foreground">
            Valor {campos.operador === "regex" && "(expressão regular)"}
            <Input
              value={campos.valor}
              onChange={mudar("valor")}
              placeholder={campos.operador === "regex" ? "^UBER.*" : "ifood"}
            />
          </label>
          <label className="space-y-1 text-xs text-muted-foreground">
            Restrita à conta (opcional)
            <select value={campos.contaId} onChange={mudar("contaId")} className={classeSelect}>
              <option value="">Qualquer conta</option>
              {contas.map((conta) => (
                <option key={conta.id} value={String(conta.id)}>
                  {conta.apelido || conta.nome}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1 text-xs text-muted-foreground">
            Restrita ao cartão (opcional)
            <select value={campos.cartaoId} onChange={mudar("cartaoId")} className={classeSelect}>
              <option value="">Qualquer cartão</option>
              {cartoes.map((cartao) => (
                <option key={cartao.id} value={String(cartao.id)}>
                  {cartao.nome}
                </option>
              ))}
            </select>
          </label>
          <label className="space-y-1 text-xs text-muted-foreground">
            Valor mínimo em R$ (opcional)
            <Input
              value={campos.valorMinTexto}
              onChange={mudar("valorMinTexto")}
              inputMode="decimal"
              placeholder="10,00"
              className="text-right tabular-nums"
            />
          </label>
          <label className="space-y-1 text-xs text-muted-foreground">
            Valor máximo em R$ (opcional)
            <Input
              value={campos.valorMaxTexto}
              onChange={mudar("valorMaxTexto")}
              inputMode="decimal"
              placeholder="500,00"
              className="text-right tabular-nums"
            />
          </label>
          <label className="space-y-1 text-xs text-muted-foreground sm:col-span-2">
            Categoria de destino
            <select
              value={campos.categoriaId}
              onChange={mudar("categoriaId")}
              className={classeSelect}
            >
              <option value="">Escolha a categoria…</option>
              {opcoesCategoria.map((opcao) => (
                <option key={opcao.id} value={String(opcao.id)}>
                  {opcao.rotulo}
                </option>
              ))}
            </select>
          </label>
          <label className="flex items-center gap-2 text-sm sm:col-span-2">
            <input
              type="checkbox"
              checked={campos.ativa}
              onChange={(evento) =>
                setCampos((atuais) => ({ ...atuais, ativa: evento.target.checked }))
              }
              className="h-4 w-4 rounded border-white/20 bg-surface-raised accent-emerald-500"
            />
            Regra ativa
          </label>
        </div>

        {erro && (
          <p role="alert" className="text-sm text-loss">
            {erro}
          </p>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={onFechar} disabled={salvando}>
            Cancelar
          </Button>
          <Button onClick={() => void salvar()} disabled={salvando}>
            {salvando ? "Salvando…" : regra ? "Salvar regra" : "Criar regra"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// --------------------------------------------------------------------------
// Componente público
// --------------------------------------------------------------------------

export function AbaCategoriasRegras() {
  const [arvore, setArvore] = useState<CategoriaNo[]>([]);
  const [regras, setRegras] = useState<RegraCategoria[]>([]);
  const [duplicidades, setDuplicidades] = useState<LinhaDuplicidade[]>([]);
  const [contas, setContas] = useState<Conta[]>([]);
  const [cartoes, setCartoes] = useState<Cartao[]>([]);
  const [mapaTransacoes, setMapaTransacoes] = useState<ReadonlyMap<number, Transacao>>(
    new Map()
  );

  const [carregando, setCarregando] = useState<boolean>(true);
  const [erro, setErro] = useState<string | null>(null);
  const [erroAcao, setErroAcao] = useState<string | null>(null);
  const [tentativa, setTentativa] = useState<number>(0);
  const recarregar = useCallback(() => setTentativa((n) => n + 1), []);

  const [novaRaiz, setNovaRaiz] = useState<string>("");
  const [criandoRaiz, setCriandoRaiz] = useState<boolean>(false);

  const [regraEmEdicao, setRegraEmEdicao] = useState<RegraCategoria | null>(null);
  const [formularioAberto, setFormularioAberto] = useState<boolean>(false);
  const [alternandoRegraId, setAlternandoRegraId] = useState<number | null>(null);

  const [previa, setPrevia] = useState<RespostaAplicarRegras | null>(null);
  const [rodandoPrevia, setRodandoPrevia] = useState<boolean>(false);
  const [confirmandoAplicacao, setConfirmandoAplicacao] = useState<boolean>(false);
  const [resultadoAplicacao, setResultadoAplicacao] = useState<string | null>(null);

  const [mostrarResolvidas, setMostrarResolvidas] = useState<boolean>(false);
  const [resolvendoId, setResolvendoId] = useState<number | null>(null);

  const [transacaoAuditada, setTransacaoAuditada] = useState<string>("");
  const [eventosAuditoria, setEventosAuditoria] = useState<EventoAuditoria[] | null>(null);
  const [avisoAuditoria, setAvisoAuditoria] = useState<string | null>(null);
  const [consultandoAuditoria, setConsultandoAuditoria] = useState<boolean>(false);

  useEffect(() => {
    const controlador = new AbortController();
    setCarregando(true);
    setErro(null);
    void Promise.allSettled([
      listarCategorias(controlador.signal),
      listarRegras(controlador.signal),
      listarDuplicidades(controlador.signal),
      listarContas(controlador.signal),
      listarCartoes(controlador.signal),
      listarTransacoes({}, { por_pagina: 200, ordenar: "id", dir: "desc" }, controlador.signal),
    ]).then(([resArvore, resRegras, resDuplicidades, resContas, resCartoes, resTransacoes]) => {
      if (controlador.signal.aborted) return;
      if (resArvore.status === "fulfilled") {
        setArvore(extrairLista<CategoriaNo>(resArvore.value, ["arvore", "itens"]));
      } else {
        setErro(mensagemDeFalha(resArvore.reason, "Não foi possível carregar as categorias."));
      }
      if (resRegras.status === "fulfilled") {
        setRegras(extrairLista<RegraCategoria>(resRegras.value, ["itens"]));
      }
      if (resDuplicidades.status === "fulfilled") {
        setDuplicidades(extrairLista<LinhaDuplicidade>(resDuplicidades.value, ["itens"]));
      }
      if (resContas.status === "fulfilled") {
        setContas(extrairLista<Conta>(resContas.value, ["itens"]));
      }
      if (resCartoes.status === "fulfilled") {
        setCartoes(extrairLista<Cartao>(resCartoes.value, ["itens"]));
      }
      if (resTransacoes.status === "fulfilled") {
        const itens = extrairLista<Transacao>(resTransacoes.value, ["itens"]);
        const mapa = new Map<number, Transacao>();
        for (const item of itens) mapa.set(item.id, item);
        setMapaTransacoes(mapa);
      }
      setCarregando(false);
    });
    return () => controlador.abort();
  }, [tentativa]);

  const opcoesCategoria = useMemo(() => achatarCategorias(arvore), [arvore]);
  const nomePorCategoria = useMemo(() => {
    const mapa = new Map<number, string>();
    for (const opcao of opcoesCategoria) {
      mapa.set(opcao.id, opcao.rotulo.replace(/^(?:— )+/, ""));
    }
    return mapa;
  }, [opcoesCategoria]);

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
  const regraPorId = useMemo(() => {
    const mapa = new Map<number, RegraCategoria>();
    for (const regra of regras) mapa.set(regra.id, regra);
    return mapa;
  }, [regras]);

  // ----- categorias ------------------------------------------------------

  const renomearCategoria = useCallback(
    async (id: number, nome: string) => {
      setErroAcao(null);
      try {
        await chamarExtratos<unknown>("PATCH", `/categorias/${id}`, { nome });
        recarregar();
      } catch (falha: unknown) {
        setErroAcao(mensagemDeFalha(falha, "Não foi possível renomear a categoria."));
      }
    },
    [recarregar]
  );

  const criarCategoria = useCallback(
    async (paiId: number | null, nome: string) => {
      setErroAcao(null);
      try {
        await chamarExtratos<unknown>("POST", "/categorias", { nome, pai_id: paiId });
        recarregar();
      } catch (falha: unknown) {
        setErroAcao(mensagemDeFalha(falha, "Não foi possível criar a categoria."));
      }
    },
    [recarregar]
  );

  const criarRaiz = useCallback(async () => {
    if (novaRaiz.trim() === "") return;
    setCriandoRaiz(true);
    await criarCategoria(null, novaRaiz.trim());
    setNovaRaiz("");
    setCriandoRaiz(false);
  }, [novaRaiz, criarCategoria]);

  // ----- regras ----------------------------------------------------------

  const alternarAtiva = useCallback(
    async (regra: RegraCategoria) => {
      setAlternandoRegraId(regra.id);
      setErroAcao(null);
      try {
        await chamarExtratos<unknown>("PATCH", `/regras/${regra.id}`, {
          ativo: regra.ativo === 1 ? 0 : 1,
        });
        setRegras((atuais) =>
          atuais.map((item) =>
            item.id === regra.id ? { ...item, ativo: item.ativo === 1 ? 0 : 1 } : item
          )
        );
      } catch (falha: unknown) {
        setErroAcao(mensagemDeFalha(falha, "Não foi possível alterar a regra."));
      } finally {
        setAlternandoRegraId(null);
      }
    },
    []
  );

  const rodarPrevia = useCallback(async () => {
    setRodandoPrevia(true);
    setErroAcao(null);
    setResultadoAplicacao(null);
    try {
      const resposta = await chamarExtratos<RespostaAplicarRegras>(
        "POST",
        "/regras/aplicar",
        {}
      );
      setPrevia(resposta);
    } catch (falha: unknown) {
      setErroAcao(mensagemDeFalha(falha, "Não foi possível gerar a prévia das regras."));
    } finally {
      setRodandoPrevia(false);
    }
  }, []);

  const confirmarAplicacao = useCallback(async () => {
    setConfirmandoAplicacao(true);
    setErroAcao(null);
    try {
      const resposta = await chamarExtratos<RespostaAplicarRegras>(
        "POST",
        "/regras/aplicar?confirmar=1",
        {}
      );
      const aplicadas = resposta.aplicadas ?? 0;
      setResultadoAplicacao(
        `${formatarInteiro(aplicadas)} transaç${aplicadas === 1 ? "ão" : "ões"} categorizada${aplicadas === 1 ? "" : "s"} pelas regras.` +
          (resposta.avisos && resposta.avisos.length > 0
            ? ` Avisos: ${resposta.avisos.join(" · ")}`
            : "")
      );
      setPrevia(null);
      recarregar();
    } catch (falha: unknown) {
      setErroAcao(mensagemDeFalha(falha, "Não foi possível confirmar a aplicação das regras."));
    } finally {
      setConfirmandoAplicacao(false);
    }
  }, [recarregar]);

  // ----- duplicidades ----------------------------------------------------

  const resolverDuplicidade = useCallback(
    async (duplicidade: LinhaDuplicidade, resolucao: "manter" | "ignorar") => {
      setResolvendoId(duplicidade.id);
      setErroAcao(null);
      try {
        await chamarExtratos<unknown>("POST", `/duplicidades/${duplicidade.id}/resolver`, {
          resolucao,
          justificativa:
            resolucao === "manter"
              ? "revisão manual: as duas transações são legítimas"
              : "revisão manual: uma das transações foi marcada como duplicada",
        });
        setDuplicidades((atuais) =>
          atuais.map((item) =>
            item.id === duplicidade.id
              ? { ...item, resolucao, resolvida_em: new Date().toISOString() }
              : item
          )
        );
      } catch (falha: unknown) {
        setErroAcao(mensagemDeFalha(falha, "Não foi possível resolver a duplicidade."));
      } finally {
        setResolvendoId(null);
      }
    },
    []
  );

  // ----- auditoria -------------------------------------------------------

  const consultarAuditoria = useCallback(async (idTexto: string) => {
    const id = Number.parseInt(idTexto, 10);
    if (!Number.isFinite(id) || id <= 0) {
      setAvisoAuditoria("Informe o número (id) de uma transação.");
      setEventosAuditoria(null);
      return;
    }
    setConsultandoAuditoria(true);
    setAvisoAuditoria(null);
    setEventosAuditoria(null);
    try {
      const resposta = await chamarExtratos<{ auditoria?: unknown }>(
        "GET",
        `/transacoes/${id}`
      );
      if (Array.isArray(resposta.auditoria)) {
        setEventosAuditoria(resposta.auditoria as EventoAuditoria[]);
        if ((resposta.auditoria as unknown[]).length === 0) {
          setAvisoAuditoria("Nenhum evento de auditoria para esta transação ainda.");
        }
      } else {
        setAvisoAuditoria(
          "A API ainda não expõe o campo auditoria em GET /extratos/transacoes/{id}. Assim que expuser, o histórico aparece aqui."
        );
      }
    } catch (falha: unknown) {
      if (falha instanceof ErroApiExtratos && (falha.status === 404 || falha.status === 405)) {
        setAvisoAuditoria(
          "A API ainda não expõe GET /extratos/transacoes/{id} com o campo auditoria — o histórico ficará disponível quando o motor publicar essa rota."
        );
      } else {
        setAvisoAuditoria(mensagemDeFalha(falha, "Não foi possível consultar a auditoria."));
      }
    } finally {
      setConsultandoAuditoria(false);
    }
  }, []);

  const selecionarParaAuditoria = useCallback(
    (id: number) => {
      setTransacaoAuditada(String(id));
      void consultarAuditoria(String(id));
    },
    [consultarAuditoria]
  );

  // ----- render ----------------------------------------------------------

  if (erro && arvore.length === 0 && !carregando) {
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

  const duplicidadesVisiveis = duplicidades.filter(
    (item) => mostrarResolvidas || !item.resolucao
  );

  return (
    <div className="space-y-6">
      {erroAcao && (
        <p
          role="alert"
          className="rounded-xl border border-loss/30 bg-loss/5 p-3 text-sm text-loss"
        >
          {erroAcao}
        </p>
      )}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {/* ------------------------------------------------- categorias */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <FolderTree className="h-4 w-4 text-primary" aria-hidden />
              Árvore de categorias
            </CardTitle>
            <CardDescription>
              O número ao lado é o uso (transações + partes de divisões).
              Renomear e criar subcategorias é auditado no motor.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {carregando ? (
              <Skeleton className="h-40 w-full" />
            ) : arvore.length === 0 ? (
              <p className="text-sm text-muted-foreground">
                Nenhuma categoria ainda — crie a primeira abaixo ou confirme um
                lote de importação (o motor semeia as categorias padrão).
              </p>
            ) : (
              <ul aria-label="Árvore de categorias">
                {arvore.map((no) => (
                  <NoArvore
                    key={no.id}
                    no={no}
                    nivel={0}
                    onRenomear={renomearCategoria}
                    onCriarFilha={criarCategoria}
                  />
                ))}
              </ul>
            )}
            <div className="flex items-center gap-2 border-t border-white/5 pt-3">
              <Input
                value={novaRaiz}
                onChange={(evento) => setNovaRaiz(evento.target.value)}
                placeholder="Nova categoria raiz"
                aria-label="Nome da nova categoria raiz"
                className="max-w-56"
              />
              <Button
                size="sm"
                variant="outline"
                disabled={criandoRaiz || novaRaiz.trim() === ""}
                onClick={() => void criarRaiz()}
              >
                <Plus className="h-3.5 w-3.5" aria-hidden />
                Criar
              </Button>
            </div>
          </CardContent>
        </Card>

        {/* -------------------------------------------- aplicar regras */}
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <Wand2 className="h-4 w-4 text-primary" aria-hidden />
              Aplicar regras
            </CardTitle>
            <CardDescription>
              Primeiro a prévia (nada é gravado); a aplicação só acontece na
              confirmação. Categorias manuais nunca são sobrescritas.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" disabled={rodandoPrevia} onClick={() => void rodarPrevia()}>
                {rodandoPrevia ? "Gerando prévia…" : "Aplicar regras (prévia)"}
              </Button>
              {previa && (
                <>
                  <Button
                    size="sm"
                    variant="default"
                    disabled={confirmandoAplicacao || (previa.previa ?? []).length === 0}
                    onClick={() => void confirmarAplicacao()}
                  >
                    <Check className="h-3.5 w-3.5" aria-hidden />
                    {confirmandoAplicacao ? "Aplicando…" : "Confirmar aplicação"}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setPrevia(null)}>
                    Descartar prévia
                  </Button>
                </>
              )}
            </div>

            {resultadoAplicacao && (
              <p
                aria-live="polite"
                className="rounded-xl border border-primary/20 bg-primary/5 p-3 text-sm"
              >
                {resultadoAplicacao}
              </p>
            )}

            {previa && (
              <>
                <p className="text-sm text-muted-foreground" aria-live="polite">
                  {formatarInteiro((previa.previa ?? []).length)} transaç
                  {(previa.previa ?? []).length === 1 ? "ão" : "ões"} seria
                  {(previa.previa ?? []).length === 1 ? "" : "m"} categorizada
                  {(previa.previa ?? []).length === 1 ? "" : "s"}. Nada foi gravado ainda.
                </p>
                {(previa.avisos ?? []).map((aviso, indice) => (
                  <p key={indice} className="text-xs text-amber-400">
                    {aviso}
                  </p>
                ))}
                {(previa.previa ?? []).length > 0 && (
                  <div className="max-h-72 overflow-x-auto overflow-y-auto rounded-xl border border-white/10">
                    <Table>
                      <TableHeader>
                        <TableRow>
                          <TableHead scope="col">Transação</TableHead>
                          <TableHead scope="col">Categoria atual → sugerida</TableHead>
                          <TableHead scope="col">Regra</TableHead>
                          <TableHead scope="col">Confiança</TableHead>
                        </TableRow>
                      </TableHeader>
                      <TableBody>
                        {(previa.previa ?? []).map((item) => {
                          const transacao = mapaTransacoes.get(item.transacao_id);
                          const regra = regraPorId.get(item.regra_id);
                          return (
                            <TableRow key={item.transacao_id}>
                              <TableCell>
                                <button
                                  type="button"
                                  onClick={() => selecionarParaAuditoria(item.transacao_id)}
                                  className="text-left underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                                  aria-label={`Ver histórico da transação ${item.transacao_id}`}
                                >
                                  {transacao ? (
                                    <>
                                      <span className="block max-w-[16rem] truncate">
                                        {textoOuTraco(transacao.descricao_original)}
                                      </span>
                                      <span className="block text-[11px] tabular-nums text-muted-foreground">
                                        {formatarDataISO(transacao.data_operacao)} ·{" "}
                                        {formatarCentavos(transacao.valor_centavos)}
                                      </span>
                                    </>
                                  ) : (
                                    <span className="tabular-nums">#{item.transacao_id}</span>
                                  )}
                                </button>
                              </TableCell>
                              <TableCell className="whitespace-nowrap text-sm">
                                <span className="text-muted-foreground">
                                  {item.categoria_atual !== null
                                    ? (nomePorCategoria.get(item.categoria_atual) ??
                                      `#${item.categoria_atual}`)
                                    : "Sem categoria"}
                                </span>{" "}
                                →{" "}
                                <span className="text-primary">
                                  {nomePorCategoria.get(item.categoria_sugerida) ??
                                    `#${item.categoria_sugerida}`}
                                </span>
                              </TableCell>
                              <TableCell className="whitespace-nowrap text-xs text-muted-foreground">
                                {regra
                                  ? `#${regra.id} · ${ROTULOS_CAMPO_REGRA[regra.campo] ?? regra.campo} ${ROTULOS_OPERADOR[regra.operador] ?? regra.operador} “${regra.valor}”`
                                  : `Regra #${item.regra_id}`}
                              </TableCell>
                              <TableCell className="whitespace-nowrap text-xs tabular-nums">
                                {item.confianca !== undefined
                                  ? formatarFracaoPercentual(item.confianca, 0)
                                  : "—"}
                              </TableCell>
                            </TableRow>
                          );
                        })}
                      </TableBody>
                    </Table>
                  </div>
                )}
              </>
            )}
          </CardContent>
        </Card>
      </div>

      {/* ------------------------------------------------------- regras */}
      <Card>
        <CardHeader className="flex-row items-start justify-between space-y-0 pb-2">
          <div className="space-y-1.5">
            <CardTitle className="text-base">Regras de categorização</CardTitle>
            <CardDescription>
              Aplicadas em ordem de prioridade (menor primeiro); a primeira que
              casa vence.
            </CardDescription>
          </div>
          <Button
            size="sm"
            onClick={() => {
              setRegraEmEdicao(null);
              setFormularioAberto(true);
            }}
          >
            <Plus className="h-3.5 w-3.5" aria-hidden />
            Nova regra
          </Button>
        </CardHeader>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead scope="col" className="text-right">
                    Prioridade
                  </TableHead>
                  <TableHead scope="col">Campo</TableHead>
                  <TableHead scope="col">Operador</TableHead>
                  <TableHead scope="col">Valor</TableHead>
                  <TableHead scope="col">Restrições</TableHead>
                  <TableHead scope="col">Categoria destino</TableHead>
                  <TableHead scope="col">Ativa</TableHead>
                  <TableHead scope="col">
                    <span className="sr-only">Ações</span>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {carregando ? (
                  Array.from({ length: 3 }, (_, indice) => (
                    <TableRow key={`esqueleto-regra-${indice}`}>
                      <TableCell colSpan={8}>
                        <Skeleton className="h-6 w-full" />
                      </TableCell>
                    </TableRow>
                  ))
                ) : regras.length === 0 ? (
                  <TableRow>
                    <TableCell
                      colSpan={8}
                      className="p-8 text-center text-sm text-muted-foreground"
                    >
                      Nenhuma regra ainda — crie a primeira para categorizar
                      transações automaticamente.
                    </TableCell>
                  </TableRow>
                ) : (
                  regras.map((regra) => {
                    const restricoes: string[] = [];
                    if (regra.conta_id !== null) {
                      restricoes.push(
                        `conta ${nomesConta.get(regra.conta_id) ?? `#${regra.conta_id}`}`
                      );
                    }
                    if (regra.cartao_id !== null) {
                      restricoes.push(
                        `cartão ${nomesCartao.get(regra.cartao_id) ?? `#${regra.cartao_id}`}`
                      );
                    }
                    if (regra.valor_min_centavos !== null) {
                      restricoes.push(`≥ ${formatarCentavos(regra.valor_min_centavos)}`);
                    }
                    if (regra.valor_max_centavos !== null) {
                      restricoes.push(`≤ ${formatarCentavos(regra.valor_max_centavos)}`);
                    }
                    return (
                      <TableRow key={regra.id} className={cn(regra.ativo !== 1 && "opacity-60")}>
                        <TableCell className="text-right tabular-nums">
                          {regra.prioridade}
                        </TableCell>
                        <TableCell className="whitespace-nowrap">
                          {ROTULOS_CAMPO_REGRA[regra.campo] ?? regra.campo}
                        </TableCell>
                        <TableCell className="whitespace-nowrap">
                          {ROTULOS_OPERADOR[regra.operador] ?? regra.operador}
                        </TableCell>
                        <TableCell className="max-w-[12rem]">
                          <span className="block truncate font-mono text-xs" title={regra.valor}>
                            {regra.valor}
                          </span>
                        </TableCell>
                        <TableCell className="max-w-[12rem] text-xs text-muted-foreground">
                          {restricoes.length > 0 ? restricoes.join(" · ") : "—"}
                        </TableCell>
                        <TableCell className="whitespace-nowrap">
                          {regra.categoria_nome ??
                            nomePorCategoria.get(regra.categoria_id) ??
                            `#${regra.categoria_id}`}
                        </TableCell>
                        <TableCell>
                          <input
                            type="checkbox"
                            checked={regra.ativo === 1}
                            disabled={alternandoRegraId === regra.id}
                            onChange={() => void alternarAtiva(regra)}
                            aria-label={`Regra ${regra.id} ativa`}
                            className="h-4 w-4 rounded border-white/20 bg-surface-raised accent-emerald-500"
                          />
                        </TableCell>
                        <TableCell>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8"
                            aria-label={`Editar regra ${regra.id}`}
                            onClick={() => {
                              setRegraEmEdicao(regra);
                              setFormularioAberto(true);
                            }}
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </Button>
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

      {/* -------------------------------------------------- duplicidades */}
      <Card>
        <CardHeader className="flex-row items-start justify-between space-y-0 pb-2">
          <div className="space-y-1.5">
            <CardTitle className="flex items-center gap-2 text-base">
              <Copy className="h-4 w-4 text-primary" aria-hidden />
              Fila de duplicidades
            </CardTitle>
            <CardDescription>
              Transações com a mesma impressão digital mas identificadores
              diferentes. Nada é apagado automaticamente — a decisão é sua e
              fica auditada.
            </CardDescription>
          </div>
          <label className="flex items-center gap-2 text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={mostrarResolvidas}
              onChange={(evento) => setMostrarResolvidas(evento.target.checked)}
              className="h-4 w-4 rounded border-white/20 bg-surface-raised accent-emerald-500"
            />
            Mostrar resolvidas
          </label>
        </CardHeader>
        <CardContent className="space-y-3">
          {carregando ? (
            <Skeleton className="h-24 w-full" />
          ) : duplicidadesVisiveis.length === 0 ? (
            <p className="text-sm text-muted-foreground" aria-live="polite">
              Nenhuma duplicidade {mostrarResolvidas ? "registrada" : "pendente"} — fila limpa.
            </p>
          ) : (
            <ul className="space-y-3">
              {duplicidadesVisiveis.map((duplicidade) => {
                const origem =
                  duplicidade.conta_id !== null && duplicidade.conta_id !== undefined
                    ? (nomesConta.get(duplicidade.conta_id) ?? `Conta #${duplicidade.conta_id}`)
                    : duplicidade.cartao_id !== null && duplicidade.cartao_id !== undefined
                      ? (nomesCartao.get(duplicidade.cartao_id) ??
                        `Cartão #${duplicidade.cartao_id}`)
                      : "—";
                const candidataDescricao =
                  typeof duplicidade.candidata_descricao === "string"
                    ? duplicidade.candidata_descricao
                    : null;
                return (
                  <li
                    key={duplicidade.id}
                    className="space-y-2 rounded-xl border border-white/10 bg-surface-raised p-3"
                  >
                    <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
                      <div className="rounded-lg border border-white/5 p-2.5">
                        <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
                          Transação registrada
                        </p>
                        <button
                          type="button"
                          onClick={() => selecionarParaAuditoria(duplicidade.transacao_id)}
                          className="mt-1 block w-full text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                          aria-label={`Ver histórico da transação ${duplicidade.transacao_id}`}
                        >
                          <span className="block truncate text-sm">
                            {textoOuTraco(duplicidade.descricao_original ?? null)}
                          </span>
                          <span className="block text-xs tabular-nums text-muted-foreground">
                            {formatarDataISO(duplicidade.data_operacao ?? null)} ·{" "}
                            {centavosOuTraco(duplicidade.valor_centavos ?? null)} · {origem} · #
                            {duplicidade.transacao_id}
                          </span>
                        </button>
                      </div>
                      <div className="rounded-lg border border-white/5 p-2.5">
                        <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
                          Candidata (mesma impressão digital)
                        </p>
                        <span className="mt-1 block truncate text-sm">
                          {candidataDescricao ??
                            "Lançamento equivalente detectado em outro arquivo/identificador"}
                        </span>
                        <span className="block break-all text-xs text-muted-foreground">
                          fingerprint {String(duplicidade.candidata_fingerprint).slice(0, 16)}… ·{" "}
                          {duplicidade.motivo}
                        </span>
                      </div>
                    </div>
                    {duplicidade.resolucao ? (
                      <p className="text-xs text-muted-foreground" aria-live="polite">
                        Resolvida como{" "}
                        <Badge variant={duplicidade.resolucao === "manter" ? "default" : "secondary"}>
                          {duplicidade.resolucao === "manter"
                            ? "manter ambas"
                            : duplicidade.resolucao === "ignorar"
                              ? "uma ignorada"
                              : duplicidade.resolucao}
                        </Badge>{" "}
                        em {formatarDataHoraISO(duplicidade.resolvida_em)}.
                      </p>
                    ) : (
                      <div className="flex flex-wrap items-center gap-2">
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={resolvendoId !== null}
                          onClick={() => void resolverDuplicidade(duplicidade, "manter")}
                        >
                          <Check className="h-3.5 w-3.5" aria-hidden />
                          {resolvendoId === duplicidade.id ? "Resolvendo…" : "Manter ambas"}
                        </Button>
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={resolvendoId !== null}
                          onClick={() => void resolverDuplicidade(duplicidade, "ignorar")}
                        >
                          <X className="h-3.5 w-3.5" aria-hidden />
                          Ignorar uma
                        </Button>
                      </div>
                    )}
                  </li>
                );
              })}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* ------------------------------------------------------ auditoria */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <History className="h-4 w-4 text-primary" aria-hidden />
            Histórico de auditoria da transação
          </CardTitle>
          <CardDescription>
            Clique numa transação da prévia ou da fila de duplicidades, ou
            informe o id abaixo.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-2">
            <Input
              value={transacaoAuditada}
              onChange={(evento) => setTransacaoAuditada(evento.target.value)}
              inputMode="numeric"
              placeholder="Id da transação"
              aria-label="Id da transação para consultar auditoria"
              className="w-40 tabular-nums"
            />
            <Button
              size="sm"
              variant="outline"
              disabled={consultandoAuditoria || transacaoAuditada.trim() === ""}
              onClick={() => void consultarAuditoria(transacaoAuditada)}
            >
              {consultandoAuditoria ? "Consultando…" : "Consultar"}
            </Button>
          </div>

          {avisoAuditoria && (
            <p aria-live="polite" className="text-sm text-muted-foreground">
              {avisoAuditoria}
            </p>
          )}

          {eventosAuditoria && eventosAuditoria.length > 0 && (
            <ol className="space-y-2" aria-label="Eventos de auditoria">
              {eventosAuditoria.map((evento, indice) => (
                <li
                  key={indice}
                  className="rounded-xl border border-white/10 bg-surface-raised p-3 text-sm"
                >
                  <p className="flex flex-wrap items-center gap-2">
                    <Badge variant="outline">{evento.acao ?? "evento"}</Badge>
                    <span className="text-xs tabular-nums text-muted-foreground">
                      {formatarDataHoraISO(evento.quando ?? null)}
                    </span>
                    {evento.origem && (
                      <span className="text-xs text-muted-foreground">
                        origem: {evento.origem}
                      </span>
                    )}
                  </p>
                  {(evento.campo || evento.valor_anterior || evento.valor_novo) && (
                    <p className="mt-1 text-xs text-muted-foreground">
                      {evento.campo && <span>{evento.campo}: </span>}
                      <span>{evento.valor_anterior ?? "—"}</span> →{" "}
                      <span className="text-foreground">{evento.valor_novo ?? "—"}</span>
                    </p>
                  )}
                  {evento.justificativa && (
                    <p className="mt-1 text-xs italic text-muted-foreground">
                      “{evento.justificativa}”
                    </p>
                  )}
                </li>
              ))}
            </ol>
          )}
        </CardContent>
      </Card>

      {formularioAberto && (
        <FormularioRegra
          key={regraEmEdicao?.id ?? "nova"}
          regra={regraEmEdicao}
          opcoesCategoria={opcoesCategoria}
          contas={contas}
          cartoes={cartoes}
          aberto
          onFechar={() => setFormularioAberto(false)}
          onSalvo={() => {
            setFormularioAberto(false);
            recarregar();
          }}
        />
      )}
    </div>
  );
}
