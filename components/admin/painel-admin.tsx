"use client";

/**
 * Painel da área `/admin`: carrega contas, sessões e eventos em paralelo,
 * concentra as ações (aprovar, desativar, promover, encerrar sessões) e as
 * confirmações, e recarrega tudo depois de cada mudança.
 *
 * Erros do motor (inclusive os 409 — "não dá para remover o último admin",
 * "você não pode se rebaixar"…) são mostrados exatamente como chegaram.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, RefreshCw, TriangleAlert } from "lucide-react";
import {
  alterarConta,
  type ContaAdmin,
  encerrarSessao,
  encerrarSessoesDaConta,
  type EventoAcesso,
  ErroAdmin,
  listarContas,
  listarEventos,
  listarSessoes,
  type SessaoAdmin,
  type UsuarioAtual,
} from "@/lib/auth/admin-api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { AcessosRecentes } from "@/components/admin/acessos-recentes";
import { ContasPendentes } from "@/components/admin/contas-pendentes";
import { DialogoConfirmar, type PedidoConfirmacao } from "@/components/admin/dialogo-confirmar";
import { SessoesAtivas } from "@/components/admin/sessoes-ativas";
import { TabelaContas } from "@/components/admin/tabela-contas";

interface Props {
  usuarioAtual: UsuarioAtual;
  /** Chamado quando o motor responde 401 (sessão caiu no meio do uso). */
  onSessaoExpirada: () => void;
}

const LIMITE_EVENTOS = 100;

function mensagemDe(falha: unknown): string {
  if (falha instanceof Error && falha.message) return falha.message;
  return "Não foi possível concluir a ação.";
}

export function PainelAdmin({ usuarioAtual, onSessaoExpirada }: Props) {
  const [contas, setContas] = useState<ContaAdmin[]>([]);
  const [carregandoContas, setCarregandoContas] = useState(true);
  const [erroContas, setErroContas] = useState<string | null>(null);

  const [sessoes, setSessoes] = useState<SessaoAdmin[]>([]);
  const [carregandoSessoes, setCarregandoSessoes] = useState(true);
  const [erroSessoes, setErroSessoes] = useState<string | null>(null);

  const [eventos, setEventos] = useState<EventoAcesso[]>([]);
  const [carregandoEventos, setCarregandoEventos] = useState(true);
  const [erroEventos, setErroEventos] = useState<string | null>(null);
  const [filtroEmail, setFiltroEmail] = useState("");
  // O filtro aplicado só muda quando o admin envia o formulário.
  const filtroAplicado = useRef("");

  const [contaOcupada, setContaOcupada] = useState<number | null>(null);
  const [sessaoOcupada, setSessaoOcupada] = useState<number | null>(null);
  const [pedido, setPedido] = useState<PedidoConfirmacao | null>(null);
  const [aviso, setAviso] = useState<{ tipo: "ok" | "erro"; texto: string } | null>(null);

  const tratarFalha = useCallback(
    (falha: unknown, aplicar: (mensagem: string) => void) => {
      if (falha instanceof DOMException && falha.name === "AbortError") return;
      if (falha instanceof ErroAdmin && falha.status === 401) {
        onSessaoExpirada();
        return;
      }
      aplicar(mensagemDe(falha));
    },
    [onSessaoExpirada]
  );

  const carregarContas = useCallback(
    async (signal?: AbortSignal) => {
      setCarregandoContas(true);
      setErroContas(null);
      try {
        setContas(await listarContas(signal));
      } catch (falha) {
        tratarFalha(falha, setErroContas);
      } finally {
        if (!signal?.aborted) setCarregandoContas(false);
      }
    },
    [tratarFalha]
  );

  const carregarSessoes = useCallback(
    async (signal?: AbortSignal) => {
      setCarregandoSessoes(true);
      setErroSessoes(null);
      try {
        setSessoes(await listarSessoes(undefined, signal));
      } catch (falha) {
        tratarFalha(falha, setErroSessoes);
      } finally {
        if (!signal?.aborted) setCarregandoSessoes(false);
      }
    },
    [tratarFalha]
  );

  const carregarEventos = useCallback(
    async (signal?: AbortSignal) => {
      setCarregandoEventos(true);
      setErroEventos(null);
      try {
        setEventos(
          await listarEventos(
            { limite: LIMITE_EVENTOS, email: filtroAplicado.current },
            signal
          )
        );
      } catch (falha) {
        tratarFalha(falha, setErroEventos);
      } finally {
        if (!signal?.aborted) setCarregandoEventos(false);
      }
    },
    [tratarFalha]
  );

  const recarregarTudo = useCallback(
    (signal?: AbortSignal) =>
      Promise.all([carregarContas(signal), carregarSessoes(signal), carregarEventos(signal)]),
    [carregarContas, carregarSessoes, carregarEventos]
  );

  useEffect(() => {
    const controlador = new AbortController();
    void recarregarTudo(controlador.signal);
    return () => controlador.abort();
  }, [recarregarTudo]);

  // Aviso de sucesso some sozinho; o de erro fica até a próxima ação.
  useEffect(() => {
    if (aviso?.tipo !== "ok") return;
    const temporizador = window.setTimeout(() => setAviso(null), 6000);
    return () => window.clearTimeout(temporizador);
  }, [aviso]);

  /** Executa uma ação sobre uma conta e recarrega tudo depois. */
  const executarNaConta = useCallback(
    async (conta: ContaAdmin, acao: () => Promise<string>) => {
      setContaOcupada(conta.id);
      setAviso(null);
      try {
        const texto = await acao();
        setAviso({ tipo: "ok", texto });
        await recarregarTudo();
      } catch (falha) {
        if (falha instanceof ErroAdmin && falha.status === 401) {
          onSessaoExpirada();
          return;
        }
        // Repropaga para o diálogo mostrar (ou para o aviso, quando sem diálogo).
        throw falha;
      } finally {
        setContaOcupada(null);
      }
    },
    [onSessaoExpirada, recarregarTudo]
  );

  const nomeDe = (conta: ContaAdmin) => conta.nome?.trim() || conta.email;

  // ---- Ações diretas (sem confirmação) -----------------------------------

  const aprovar = useCallback(
    (conta: ContaAdmin) => {
      executarNaConta(conta, async () => {
        await alterarConta(conta.id, { ativo: true });
        return `Conta ${conta.email} aprovada. A pessoa já pode entrar.`;
      }).catch((falha) => setAviso({ tipo: "erro", texto: mensagemDe(falha) }));
    },
    [executarNaConta]
  );

  // ---- Ações com confirmação --------------------------------------------

  const desativar = useCallback(
    (conta: ContaAdmin) =>
      setPedido({
        titulo: `Desativar ${nomeDe(conta)}?`,
        descricao: `A conta ${conta.email} deixa de entrar nas plataformas e todas as sessões dela são encerradas agora. Você pode aprovar de novo depois.`,
        rotuloConfirmar: "Desativar conta",
        destrutivo: true,
        confirmar: () =>
          executarNaConta(conta, async () => {
            await alterarConta(conta.id, { ativo: false });
            return `Conta ${conta.email} desativada.`;
          }),
      }),
    [executarNaConta]
  );

  const tornarAdmin = useCallback(
    (conta: ContaAdmin) =>
      setPedido({
        titulo: `Tornar ${nomeDe(conta)} administrador?`,
        descricao: `Administradores aprovam contas, desativam pessoas e encerram sessões de qualquer conta — inclusive a sua.`,
        rotuloConfirmar: "Tornar admin",
        confirmar: () =>
          executarNaConta(conta, async () => {
            await alterarConta(conta.id, { admin: true });
            return `${conta.email} agora é administrador.`;
          }),
      }),
    [executarNaConta]
  );

  const removerAdmin = useCallback(
    (conta: ContaAdmin) =>
      setPedido({
        titulo: `Remover admin de ${nomeDe(conta)}?`,
        descricao: `A conta ${conta.email} continua ativa, mas perde o acesso a esta área de administração.`,
        rotuloConfirmar: "Remover admin",
        destrutivo: true,
        confirmar: () =>
          executarNaConta(conta, async () => {
            await alterarConta(conta.id, { admin: false });
            return `${conta.email} deixou de ser administrador.`;
          }),
      }),
    [executarNaConta]
  );

  const encerrarSessoesDe = useCallback(
    (conta: ContaAdmin) =>
      setPedido({
        titulo: `Encerrar as sessões de ${nomeDe(conta)}?`,
        descricao: `A conta ${conta.email} é derrubada de todos os dispositivos (${conta.sessoes_ativas} sessão(ões) ativa(s)) e precisa entrar de novo.${
          conta.id === usuarioAtual.id ? " Isso inclui a sessão que você está usando agora." : ""
        }`,
        rotuloConfirmar: "Encerrar sessões",
        destrutivo: true,
        confirmar: () =>
          executarNaConta(conta, async () => {
            const resultado = await encerrarSessoesDaConta(conta.id);
            return `${resultado.sessoes_encerradas} sessão(ões) de ${conta.email} encerrada(s).`;
          }),
      }),
    [executarNaConta, usuarioAtual.id]
  );

  const encerrarUmaSessao = useCallback(
    (sessao: SessaoAdmin) =>
      setPedido({
        titulo: "Encerrar esta sessão?",
        descricao: `A sessão de ${sessao.email} neste dispositivo é encerrada e a pessoa precisa entrar de novo lá.${
          sessao.usuario_id === usuarioAtual.id ? " Se for a sua sessão atual, você volta para a tela de login." : ""
        }`,
        rotuloConfirmar: "Encerrar sessão",
        destrutivo: true,
        confirmar: async () => {
          setSessaoOcupada(sessao.id);
          setAviso(null);
          try {
            await encerrarSessao(sessao.id);
            setAviso({ tipo: "ok", texto: `Sessão de ${sessao.email} encerrada.` });
            await Promise.all([carregarSessoes(), carregarContas()]);
          } catch (falha) {
            if (falha instanceof ErroAdmin && falha.status === 401) {
              onSessaoExpirada();
              return;
            }
            throw falha;
          } finally {
            setSessaoOcupada(null);
          }
        },
      }),
    [carregarContas, carregarSessoes, onSessaoExpirada, usuarioAtual.id]
  );

  const buscarEventos = useCallback(() => {
    filtroAplicado.current = filtroEmail.trim();
    void carregarEventos();
  }, [carregarEventos, filtroEmail]);

  const pendentes = contas.filter((conta) => !conta.ativo);
  const recarregando = carregandoContas || carregandoSessoes || carregandoEventos;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          Você está como <span className="font-medium text-foreground">{usuarioAtual.email}</span>.
        </p>
        <Button
          variant="outline"
          size="sm"
          onClick={() => void recarregarTudo()}
          disabled={recarregando}
        >
          <RefreshCw className={recarregando ? "animate-spin" : undefined} aria-hidden />
          Recarregar tudo
        </Button>
      </div>

      <div aria-live="polite" aria-atomic="true">
        {aviso && (
          <Card
            role={aviso.tipo === "erro" ? "alert" : "status"}
            className={
              aviso.tipo === "erro"
                ? "border-loss/30 bg-loss/5"
                : "border-primary/30 bg-primary/5"
            }
          >
            <CardContent className="flex items-start gap-3 p-4 text-sm">
              {aviso.tipo === "erro" ? (
                <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-loss" aria-hidden />
              ) : (
                <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden />
              )}
              <span className="text-foreground">{aviso.texto}</span>
            </CardContent>
          </Card>
        )}
      </div>

      {erroContas ? (
        <Card role="alert" className="border-loss/30 bg-loss/5">
          <CardContent className="flex flex-wrap items-center justify-between gap-3 p-4 text-sm">
            <span className="flex items-start gap-2 text-foreground">
              <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-loss" aria-hidden />
              {erroContas}
            </span>
            <Button variant="outline" size="sm" onClick={() => void carregarContas()}>
              Tentar de novo
            </Button>
          </CardContent>
        </Card>
      ) : (
        <ContasPendentes contas={pendentes} ocupadoId={contaOcupada} onAprovar={aprovar} />
      )}

      <TabelaContas
        contas={contas}
        carregando={carregandoContas && contas.length === 0}
        usuarioAtualId={usuarioAtual.id}
        ocupadoId={contaOcupada}
        onAprovar={aprovar}
        onDesativar={desativar}
        onTornarAdmin={tornarAdmin}
        onRemoverAdmin={removerAdmin}
        onEncerrarSessoes={encerrarSessoesDe}
      />

      <SessoesAtivas
        sessoes={sessoes}
        carregando={carregandoSessoes}
        erro={erroSessoes}
        ocupadoId={sessaoOcupada}
        onEncerrar={encerrarUmaSessao}
        onRecarregar={() => void carregarSessoes()}
      />

      <AcessosRecentes
        eventos={eventos}
        carregando={carregandoEventos}
        erro={erroEventos}
        filtroEmail={filtroEmail}
        onFiltroEmailChange={setFiltroEmail}
        onBuscar={buscarEventos}
      />

      <DialogoConfirmar pedido={pedido} onFechar={() => setPedido(null)} />
    </div>
  );
}
