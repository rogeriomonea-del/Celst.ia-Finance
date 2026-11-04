"use client";

/**
 * Tela de entrada: e-mail → código de 6 dígitos (ou Google).
 *
 * Fluxo: digita o e-mail → "Enviar código" → campo de 6 dígitos (autofoco,
 * aceita colar, Enter envia) → sucesso: volta para `?voltar=` (ou `/`).
 * Com sessão válida já presente, redireciona direto. Nunca registra e-mail,
 * código ou token no console.
 */

import {
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  AlertTriangle,
  ArrowLeft,
  Clock,
  Info,
  KeyRound,
  Loader2,
  Mail,
  Sparkles,
} from "lucide-react";
import {
  ErroAuth,
  destinoSeguro,
  obterConfig,
  pedirCodigo,
  urlLoginGoogle,
  verificarCodigo,
  type ConfigAuth,
} from "@/lib/auth/api";
import { useUsuario } from "@/components/auth/usuario-provider";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader } from "@/components/ui/card";

const SEGUNDOS_REENVIO = 60;
const TAMANHO_CODIGO = 6;

const MENSAGENS_ERRO_URL: Record<string, string> = {
  google_cancelado:
    "O login com Google foi cancelado ou não pôde ser concluído. Tente de novo ou entre com o código por e-mail.",
  google_expirado:
    "O login com Google demorou demais ou foi aberto em outro navegador. Comece de novo por aqui.",
  google_falhou:
    "O Google não concluiu o login. Tente de novo ou entre com o código por e-mail.",
  conta_inativa:
    "Sua conta foi criada e aguarda aprovação do administrador. Você receberá acesso assim que ela for liberada.",
};

type Etapa = "email" | "codigo";

interface Aviso {
  tipo: "erro" | "pendente" | "info";
  texto: string;
}

function GoogleIcone({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true" className={className}>
      <path
        fill="#EA4335"
        d="M12 10.2v3.9h5.5c-.2 1.3-1.6 3.8-5.5 3.8-3.3 0-6-2.7-6-6.1s2.7-6.1 6-6.1c1.9 0 3.1.8 3.9 1.5l2.6-2.6C16.9 3 14.7 2 12 2 6.5 2 2 6.5 2 12s4.5 10 10 10c5.8 0 9.6-4.1 9.6-9.8 0-.7-.1-1.2-.2-1.7H12z"
      />
    </svg>
  );
}

function CaixaAviso({ aviso }: { aviso: Aviso | null }) {
  // `aria-live` fica sempre montado para o leitor de tela anunciar as mudanças.
  return (
    <div aria-live="polite" aria-atomic="true" className="min-h-0">
      {aviso && (
        <div
          role={aviso.tipo === "erro" ? "alert" : "status"}
          className={cn(
            "flex items-start gap-2.5 rounded-xl border px-3.5 py-3 text-sm leading-relaxed animate-fade-in",
            aviso.tipo === "erro" && "border-loss/30 bg-loss/10 text-rose-200",
            aviso.tipo === "pendente" && "border-amber-500/30 bg-amber-500/10 text-amber-200",
            aviso.tipo === "info" && "border-white/10 bg-white/[0.04] text-muted-foreground"
          )}
        >
          {aviso.tipo === "erro" && <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />}
          {aviso.tipo === "pendente" && <Clock className="mt-0.5 h-4 w-4 shrink-0" />}
          {aviso.tipo === "info" && <Info className="mt-0.5 h-4 w-4 shrink-0" />}
          <span>{aviso.texto}</span>
        </div>
      )}
    </div>
  );
}

function FormularioEntrada() {
  const router = useRouter();
  const parametros = useSearchParams();
  const { usuario, carregando: carregandoUsuario, definirUsuario } = useUsuario();

  const voltar = destinoSeguro(parametros.get("voltar"));
  const erroDaUrl = parametros.get("erro");

  const [config, setConfig] = useState<ConfigAuth | null>(null);
  const [etapa, setEtapa] = useState<Etapa>("email");
  const [email, setEmail] = useState("");
  const [codigo, setCodigo] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [aviso, setAviso] = useState<Aviso | null>(() => {
    const texto = erroDaUrl ? MENSAGENS_ERRO_URL[erroDaUrl] : undefined;
    if (!texto) return null;
    return { tipo: erroDaUrl === "conta_inativa" ? "pendente" : "erro", texto };
  });
  const [modoLog, setModoLog] = useState(false);
  const [segundosReenvio, setSegundosReenvio] = useState(0);
  const campoCodigoRef = useRef<HTMLInputElement>(null);

  // Já existe sessão válida? Então não há o que fazer aqui.
  useEffect(() => {
    if (!carregandoUsuario && usuario) {
      router.replace(voltar);
    }
  }, [carregandoUsuario, usuario, router, voltar]);

  // Configuração pública (Google ativo? SMTP real?).
  useEffect(() => {
    const controlador = new AbortController();
    obterConfig(controlador.signal)
      .then(setConfig)
      .catch(() => setConfig(null));
    return () => controlador.abort();
  }, []);

  // Contador de reenvio.
  useEffect(() => {
    if (segundosReenvio <= 0) return;
    const temporizador = window.setTimeout(
      () => setSegundosReenvio((s) => Math.max(0, s - 1)),
      1000
    );
    return () => window.clearTimeout(temporizador);
  }, [segundosReenvio]);

  // Foco no campo do código assim que ele aparece.
  useEffect(() => {
    if (etapa === "codigo") campoCodigoRef.current?.focus();
  }, [etapa]);

  const tratarFalha = useCallback((falha: unknown) => {
    if (falha instanceof ErroAuth) {
      setAviso({ tipo: falha.contaInativa ? "pendente" : "erro", texto: falha.message });
      return;
    }
    setAviso({
      tipo: "erro",
      texto: "Algo deu errado ao falar com o serviço de login. Tente de novo.",
    });
  }, []);

  const enviarCodigo = useCallback(async () => {
    const emailLimpo = email.trim();
    if (!emailLimpo || !emailLimpo.includes("@")) {
      setAviso({ tipo: "erro", texto: "Informe um e-mail válido para receber o código." });
      return;
    }
    setEnviando(true);
    setAviso(null);
    try {
      const resultado = await pedirCodigo(emailLimpo);
      const emLog = resultado.modo === "log";
      setModoLog(emLog);
      setEtapa("codigo");
      setCodigo("");
      setSegundosReenvio(SEGUNDOS_REENVIO);
      setAviso({
        tipo: "info",
        texto: emLog
          ? "SMTP não configurado: o código foi gravado no log do servidor. Peça a quem administra o motor para conferir o log."
          : resultado.mensagem || "Código enviado. Confira sua caixa de entrada.",
      });
    } catch (falha) {
      tratarFalha(falha);
    } finally {
      setEnviando(false);
    }
  }, [email, tratarFalha]);

  const confirmarCodigo = useCallback(async () => {
    const codigoLimpo = codigo.replace(/\D/g, "");
    if (codigoLimpo.length !== TAMANHO_CODIGO) {
      setAviso({ tipo: "erro", texto: `Digite os ${TAMANHO_CODIGO} dígitos do código.` });
      campoCodigoRef.current?.focus();
      return;
    }
    setEnviando(true);
    setAviso(null);
    try {
      const logado = await verificarCodigo(email, codigoLimpo);
      definirUsuario(logado);
      router.replace(voltar);
    } catch (falha) {
      tratarFalha(falha);
      setEnviando(false);
      campoCodigoRef.current?.focus();
      campoCodigoRef.current?.select();
    }
  }, [codigo, email, definirUsuario, router, voltar, tratarFalha]);

  const aoEnviarEmail = (evento: FormEvent<HTMLFormElement>) => {
    evento.preventDefault();
    void enviarCodigo();
  };

  const aoEnviarCodigo = (evento: FormEvent<HTMLFormElement>) => {
    evento.preventDefault();
    void confirmarCodigo();
  };

  const voltarParaEmail = () => {
    setEtapa("email");
    setCodigo("");
    setAviso(null);
  };

  const googleDisponivel = config?.google === true;
  const smtpSemEnvio = config !== null && !config.envio_real;

  if (carregandoUsuario || usuario) {
    return (
      <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        <span>Verificando sua sessão…</span>
      </div>
    );
  }

  return (
    <Card className="w-full border-white/[0.08] bg-surface/90 shadow-2xl shadow-black/40 backdrop-blur">
      <CardHeader className="space-y-1.5 pb-4">
        <h1 className="text-xl font-semibold tracking-tight">
          {etapa === "email" ? "Entrar" : "Confira seu e-mail"}
        </h1>
        <p className="text-sm text-muted-foreground">
          {etapa === "email"
            ? "Informe seu e-mail e enviaremos um código de acesso. Sem senha para lembrar."
            : "Digite o código de 6 dígitos que enviamos. Ele vale por poucos minutos."}
        </p>
      </CardHeader>

      <CardContent className="space-y-5">
        {etapa === "email" ? (
          <form onSubmit={aoEnviarEmail} className="space-y-4" noValidate>
            <div className="space-y-2">
              <Label htmlFor="email">E-mail</Label>
              <div className="relative">
                <Mail
                  className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                  aria-hidden="true"
                />
                <Input
                  id="email"
                  name="email"
                  type="email"
                  inputMode="email"
                  autoComplete="email"
                  autoFocus
                  required
                  placeholder="voce@exemplo.com"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  disabled={enviando}
                  className="h-11 pl-9"
                />
              </div>
            </div>

            <CaixaAviso aviso={aviso} />

            <Button type="submit" size="lg" className="w-full" disabled={enviando}>
              {enviando ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : (
                <KeyRound aria-hidden="true" />
              )}
              {enviando ? "Enviando…" : "Enviar código"}
            </Button>

            {smtpSemEnvio && (
              <p className="text-center text-[11px] leading-relaxed text-muted-foreground/80">
                Este servidor está sem SMTP: o código será gravado no log do motor, não enviado por e-mail.
              </p>
            )}

            <div className="relative py-1">
              <div className="absolute inset-0 flex items-center" aria-hidden="true">
                <div className="w-full border-t border-white/[0.08]" />
              </div>
              <div className="relative flex justify-center">
                <span className="bg-surface px-3 text-[11px] uppercase tracking-wider text-muted-foreground">
                  ou
                </span>
              </div>
            </div>

            {googleDisponivel ? (
              <Button asChild variant="outline" size="lg" className="w-full">
                <a href={urlLoginGoogle(voltar)}>
                  <GoogleIcone className="h-4 w-4" />
                  Entrar com Google
                </a>
              </Button>
            ) : (
              <p className="text-center text-xs text-muted-foreground/70">
                {config === null
                  ? "Login com Google indisponível no momento."
                  : "Google não configurado neste servidor."}
              </p>
            )}
          </form>
        ) : (
          <form onSubmit={aoEnviarCodigo} className="space-y-4" noValidate>
            <div className="flex items-center justify-between gap-3 rounded-xl border border-white/[0.08] bg-white/[0.03] px-3.5 py-2.5">
              <div className="min-w-0">
                <p className="text-[11px] uppercase tracking-wider text-muted-foreground">
                  Enviado para
                </p>
                <p className="truncate text-sm font-medium" title={email}>
                  {email.trim()}
                </p>
              </div>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={voltarParaEmail}
                disabled={enviando}
              >
                <ArrowLeft aria-hidden="true" />
                Trocar
              </Button>
            </div>

            <div className="space-y-2">
              <Label htmlFor="codigo">Código de acesso</Label>
              <Input
                ref={campoCodigoRef}
                id="codigo"
                name="codigo"
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                autoComplete="one-time-code"
                maxLength={TAMANHO_CODIGO}
                placeholder="••••••"
                aria-describedby="codigo-ajuda"
                value={codigo}
                onChange={(e) =>
                  setCodigo(e.target.value.replace(/\D/g, "").slice(0, TAMANHO_CODIGO))
                }
                onPaste={(e) => {
                  const colado = e.clipboardData.getData("text").replace(/\D/g, "");
                  if (colado) {
                    e.preventDefault();
                    setCodigo(colado.slice(0, TAMANHO_CODIGO));
                  }
                }}
                disabled={enviando}
                className="h-14 text-center font-mono text-2xl tracking-[0.5em] placeholder:tracking-[0.5em]"
              />
              <p id="codigo-ajuda" className="text-xs text-muted-foreground">
                {modoLog
                  ? "Sem SMTP, o código está no log do servidor (journalctl ou terminal do motor)."
                  : "Não chegou? Confira a caixa de spam ou reenvie abaixo."}
              </p>
            </div>

            <CaixaAviso aviso={aviso} />

            <Button
              type="submit"
              size="lg"
              className="w-full"
              disabled={enviando || codigo.length !== TAMANHO_CODIGO}
            >
              {enviando ? <Loader2 className="animate-spin" aria-hidden="true" /> : null}
              {enviando ? "Entrando…" : "Entrar"}
            </Button>

            <div className="flex items-center justify-center">
              <Button
                type="button"
                variant="link"
                size="sm"
                className="text-muted-foreground"
                onClick={() => void enviarCodigo()}
                disabled={enviando || segundosReenvio > 0}
              >
                {segundosReenvio > 0
                  ? `Reenviar código em ${segundosReenvio}s`
                  : "Reenviar código"}
              </Button>
            </div>
          </form>
        )}
      </CardContent>
    </Card>
  );
}

function MarcaEntrada() {
  return (
    <div className="mb-8 flex flex-col items-center gap-3 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/15 ring-1 ring-primary/30">
        <Sparkles className="h-6 w-6 text-primary" aria-hidden="true" />
      </div>
      <div>
        <p className="text-lg font-semibold tracking-tight">celest.ia</p>
        <p className="text-xs text-muted-foreground">Inteligência de Investimentos</p>
      </div>
    </div>
  );
}

export default function PaginaEntrar() {
  return (
    <div className="relative flex min-h-screen items-center justify-center overflow-hidden bg-background px-4 py-12">
      {/* Brilho esmeralda discreto ao fundo. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -top-40 left-1/2 h-[480px] w-[720px] -translate-x-1/2 rounded-full bg-primary/10 blur-3xl"
      />
      <div className="relative w-full max-w-sm animate-fade-in-up">
        <MarcaEntrada />
        <Suspense
          fallback={
            <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              <span>Carregando…</span>
            </div>
          }
        >
          <FormularioEntrada />
        </Suspense>
        <p className="mt-6 text-center text-[11px] leading-relaxed text-muted-foreground/60">
          Uso educacional — não é recomendação de investimento.
        </p>
      </div>
    </div>
  );
}
