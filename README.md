# celest.ia Financeiro — Plataforma de Análise Financeira e Inteligência Multi-Agente

Plataforma web de análise de investimentos construída com **Next.js 14 (App
Router) + TypeScript + Tailwind CSS**, com sistema multi-agente de IA para
triagem fundamentalista de ações da B3, consolidação de carteira, perfil de
investidor, rebalanceamento e organizador financeiro com Open Finance simulado.

## Módulos

| Rota | Módulo | Descrição |
|---|---|---|
| `/` | **Dashboard** | Upload drag-and-drop de extratos (.csv/.xlsx/.json) com parser inteligente de colunas, carteira unificada com ganho/perda, cotações via brapi.dev, gráficos de alocação (donut), evolução patrimonial (área) e volatilidade (barras) |
| `/importar` | **Importe suas Planilhas** | Sobe uma planilha do Excel (.xlsm/.xlsx) ou .csv e devolve a análise completa — carteira, proventos, rebalanceamento, cenários, simulador, projeção 2030, fluxo de caixa, patrimônio consolidado e 9 gráficos — calculada pelo motor Python em `engine/` |
| `/agentes` | **Análise Multi-Agente** | Pipeline sequencial-paralelo com 5 agentes (Triagem Técnica ∥ Pesquisa de Contexto → Auditor de Filtros → Analista de DRE → Comitê de Investimento), terminal de operações com logs em tempo real e relatório final Top 5 |
| `/perfil` | **Perfil do Investidor** | Questionário de suitability com 7 perguntas, score Conservador/Moderado/Arrojado e alocação recomendada por classe |
| `/rebalanceamento` | **Rebalanceamento** | Porcentagens alvo por classe ou por ativo, cálculo de desvio e plano exato de COMPRAR/VENDER (valores e quantidades) |
| `/organizador` | **Organizador Financeiro** | Fluxo de caixa (receitas vs. despesas) com gráfico mensal, categorias e conexão bancária simulada via Open Finance (Itaú, Bradesco, BB, Nubank, Santander) |

## Arquitetura

```text
app/
├── layout.tsx                     # Shell com sidebar responsivo e tema dark
├── page.tsx                       # Dashboard
├── importar/page.tsx              # Importe suas Planilhas (motor Python)
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
├── importar/                      # Dropzone, KPIs e gráficos da análise da planilha
├── agent-terminal.tsx             # Terminal de operações + tracker do pipeline
├── file-dropzone.tsx              # Upload drag-and-drop
├── open-finance-modal.tsx         # Fluxo de consentimento simulado
└── portfolio-table.tsx / stat-card.tsx / sidebar.tsx
engine/                            # Motor Python 3.11 (stdlib + openpyxl; sem pandas/numpy)
├── models.py                      # Dataclasses de entrada e saída — o contrato
├── workbook.py                    # Leitura de .xlsm/.xlsx/.csv (único módulo com openpyxl)
├── extract.py                     # Abas → tipos, guiado por cabeçalhos e não por endereços
├── calc/                          # Módulos puros: posicao, proventos, rebalance, simulador,
│                                  # projecao, cenarios, fluxo, consolidado, charts
├── quotes.py                      # Cotações da brapi.dev em lote (substitui o macro VBA)
├── pipeline.py                    # Orquestra os cálculos → Analysis serializável
└── cli.py / server.py             # Uso local: terminal e servidor de desenvolvimento
api/planilha.py                    # Função serverless (@vercel/python): POST /api/planilha
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

## Importe suas Planilhas — o motor Python

A aba `/importar` recebe a planilha do usuário e devolve a análise pronta. Por
trás dela está o pacote `engine/`, que substitui uma pasta de trabalho `.xlsm` de
**17 abas, 28.430 fórmulas, 3 macros VBA e 9 gráficos** por Python puro.

O que muda na prática:

- **Sem Excel e sem macro.** O motor lê apenas os *valores* das células — nenhuma
  fórmula do arquivo é reavaliada. Toda a lógica foi reescrita em `engine/calc/`.
- **Sem AppleScript.** O macro `AtualizarCotacoes` só funcionava no macOS (via
  `AppleScriptTask` e um `.scpt` instalado à mão no Terminal) e disparava **uma
  requisição por ticker**. Virou `engine/quotes.py`: uma chamada HTTP **em lote**
  à brapi.dev a cada 20 tickers, com `json.loads` de verdade e sem exceção em
  caso de rede fora.
- **Mais rápido.** Uma passada O(n) sobre os dados no lugar de um grafo de 28.430
  fórmulas recalculadas em cascata, sem `OFFSET` volátil e sem os ~4.500
  `IFERROR` de defesa.
- **Privacidade.** O arquivo é processado **em memória** — não toca o disco, não é
  gravado, não é logado e não é versionado (`*.xlsm`/`*.xlsx` estão no
  `.gitignore`). Só os *tickers* saem para a brapi; valores e saldos, nunca.

Rodando o motor localmente (Python 3.11):

```bash
pip install -r requirements.txt          # openpyxl==3.1.5, nada mais
python3 -m engine.cli arquivo.xlsm       # análise completa em JSON no terminal
python3 -m engine.server                 # http://127.0.0.1:8000 para o front-end
python3 -m pytest tests/ -q              # fixtures sintéticas, sem dados reais
```

Com o servidor no ar, suba o Next.js apontado para ele —
`NEXT_PUBLIC_ENGINE_URL=http://127.0.0.1:8000 npm run dev`. Em produção a
variável fica vazia e as chamadas caem na função serverless `api/planilha.py`
(`POST /api/planilha`, limite de 6 MB, `.xlsm`/`.xlsx`/`.csv`).

Detalhes da conversão — mapa aba a aba, o destino de cada macro e de cada família
de fórmula, e as garantias de privacidade — em
[`docs/planilha-para-python.md`](docs/planilha-para-python.md).

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
