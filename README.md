# celest.ia Financeiro — Plataforma de Análise Financeira e Inteligência Multi-Agente

> **⚠️ STATUS: INTEGRAÇÃO PARCIAL COM A API REAL.** As áreas **`/politica`
> (Perfil e Política/IPS), `/importacao` (Importação B3), `/carteira` e
> `/plano-aportes`** são conectadas à **API real** do backend do Investment
> Intelligence OS (repositório `Celest.ia-v2-Alpha`, Fase 5) — elas não usam
> nenhum dado simulado e exibem estado de erro explícito quando o backend
> está fora do ar (`uvicorn investment_os.api.main:app`). As **demais áreas
> antigas** (Dashboard, Agentes IA, Perfil, Rebalanceamento, Organizador)
> **continuam sendo protótipo com dados simulados** (base fundamentalista
> embutida, curva patrimonial sintética, Open Finance simulado, cotações de
> agregador) e estão marcadas com o selo "Simulado" — nenhum número dessas
> áreas deve ser tratado como real. Ver `docs/REPOSITORY_AUDIT.md` e
> `docs/INTEGRATION.md`.


Plataforma web de análise de investimentos construída com **Next.js 14 (App
Router) + TypeScript + Tailwind CSS**, com sistema multi-agente de IA para
triagem fundamentalista de ações da B3, consolidação de carteira, perfil de
investidor, rebalanceamento e organizador financeiro com Open Finance simulado.

## Módulos conectados à API real (Fase 5)

| Rota | Módulo | Descrição |
|---|---|---|
| `/politica` | **Perfil e Política (IPS)** | Questionário adaptativo (scores por dimensão, conflitos, confiança), geração/edição de rascunhos da IPS em JSON, histórico de versões e confirmação explícita |
| `/importacao` | **Importação B3** | Upload xlsx/csv (máx. 10 MB), prévia com contagens e remoção de PII, correção de linhas ambíguas/desconhecidas, confirmação com aceite de importação parcial |
| `/carteira` | **Carteira** | Snapshot versionado: patrimônio precificado com nota de cobertura, pesos por classe/setor/moeda/país/emissor, posições com data do pregão, violações da IPS e qualidade dos dados |
| `/plano-aportes` | **Plano de aportes** | Rebalanceamento por aportes segundo a IPS confirmada: destino do próximo aporte, planos de 3/6 meses, ações priorizadas com justificativa, premissas e vendas evitadas |

Esses módulos exigem o backend rodando (`uvicorn investment_os.api.main:app`
no repo `Celest.ia-v2-Alpha`) e usam `NEXT_PUBLIC_IIOS_API_URL`
(padrão `http://localhost:8000`).

## Módulos do protótipo (dados simulados — selo "Simulado")

| Rota | Módulo | Descrição |
|---|---|---|
| `/` | **Dashboard** | Upload drag-and-drop de extratos (.csv/.xlsx/.json) com parser inteligente de colunas, carteira unificada com ganho/perda, cotações via brapi.dev, gráficos de alocação (donut), evolução patrimonial (área) e volatilidade (barras) |
| `/agentes` | **Análise Multi-Agente** | Pipeline sequencial-paralelo com 5 agentes (Triagem Técnica ∥ Pesquisa de Contexto → Auditor de Filtros → Analista de DRE → Comitê de Investimento), terminal de operações com logs em tempo real e relatório final Top 5 |
| `/perfil` | **Perfil do Investidor** | Questionário de suitability com 7 perguntas, score Conservador/Moderado/Arrojado e alocação recomendada por classe |
| `/rebalanceamento` | **Rebalanceamento** | Porcentagens alvo por classe ou por ativo, cálculo de desvio e plano exato de COMPRAR/VENDER (valores e quantidades) |
| `/organizador` | **Organizador Financeiro** | Fluxo de caixa (receitas vs. despesas) com gráfico mensal, categorias e conexão bancária simulada via Open Finance (Itaú, Bradesco, BB, Nubank, Santander) |

## Arquitetura

```text
app/
├── layout.tsx                     # Shell com sidebar responsivo e tema dark
├── page.tsx                       # Dashboard
├── agentes/page.tsx               # Orquestrador multi-agente + terminal de IA
├── perfil/page.tsx                # Questionário de suitability
├── rebalanceamento/page.tsx       # Algoritmo de rebalanceamento
├── organizador/page.tsx           # Fluxo de caixa + Open Finance
└── api/
    ├── quotes/route.ts            # Proxy server-side para brapi.dev (token protegido)
    └── agents/[agentId]/route.ts  # Rotas estruturadas de cada agente
components/
├── ui/                            # Primitivas shadcn-style (Radix + CVA)
├── charts/                        # Recharts (donut, área, barras) com paleta validada p/ CVD
├── agent-terminal.tsx             # Terminal de operações + tracker do pipeline
├── file-dropzone.tsx              # Upload drag-and-drop
├── open-finance-modal.tsx         # Fluxo de consentimento simulado
└── portfolio-table.tsx / stat-card.tsx / sidebar.tsx
lib/
├── agents/                        # engine.ts (motor analítico), actions.ts (Server Actions),
│   │                              # prompts.ts, types.ts, market-data.ts (base fundamentalista)
├── api/                           # brapi.ts (cliente com fallback seguro), parsers.ts
│   │                              # (parser CSV/XLSX/JSON), open-finance.ts (mock estruturado)
├── store/app-store.tsx            # Estado global (Context) com persistência em localStorage
├── finance.ts                     # Cálculos: totais, alocação, curva patrimonial, rebalanceamento
└── types.ts / utils.ts            # Tipos de domínio e formatadores pt-BR
```

### Pipeline multi-agente

Os agentes 1 e 2 executam **em paralelo**; 3, 4 e 5 em sequência, cada um
consumindo a saída tipada dos anteriores:

1. **Triagem Técnica** — filtra o universo por DY ≥ 6%, P/L ≤ 12, P/VP ≤ 5 e ROE ≥ 12%, com nota ponderada;
2. **Pesquisa de Contexto** — dossiês com notícias, governança e bandeiras de risco + score de sentimento;
3. **Auditor de Filtros** — cruza triagem × contexto e aplica cortes severos (sentimento negativo, riscos estruturais, value traps);
4. **Analista de DRE** — lucro líquido YoY, tendência de margem e Dívida Líquida/EBITDA do último semestre;
5. **Comitê de Investimento** — score final consolidado (35/25/40), Top 5 com tese, justificativa macroeconômica e alocação somando 100%.

As execuções são expostas de duas formas: **Server Actions**
(`lib/agents/actions.ts`, usadas pela UI) e **rotas de API estruturadas**
(`POST /api/agents/{triagem|pesquisa|auditor|dre|comite}`).

## Como rodar

```bash
npm install
cp .env.example .env.local   # opcional: defina seu token do brapi.dev
npm run dev                  # http://localhost:3000
```

Build de produção e checagem de tipos:

```bash
npm run build
npm run typecheck
```

Sem token/rede, a plataforma degrada com segurança para a base fundamentalista
local (`lib/agents/market-data.ts`) — todos os módulos permanecem funcionais.

## Deploy (Vercel)

O `vercel.json` já pina o preset **Next.js** e o output `.next`. Basta conectar
este repositório a um projeto Vercel (framework autodetectado) e, opcionalmente,
definir a variável de ambiente `BRAPI_TOKEN`.

> **Aviso:** plataforma de uso exclusivamente educacional. Não constitui
> recomendação de investimento nos termos da Resolução CVM 20/2021.
