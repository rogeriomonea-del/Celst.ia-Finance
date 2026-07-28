# Auditoria do Repositório — Celst.ia-Finance

Data: 2026-07-28. Papel no sistema: **frontend** do Investment Intelligence OS
(backend no repositório irmão `Celest.ia-v2-Alpha`).

## O que existe hoje

Next.js 14 (App Router) + TypeScript + Tailwind, com 5 módulos:
Dashboard (upload CSV/XLSX/JSON), Análise Multi-Agente, Perfil (suitability),
Rebalanceamento e Organizador (Open Finance **simulado**).

## Achados críticos de integridade de dados

| Achado | Local | Severidade |
|---|---|---|
| Base "fundamentalista" estática embutida no código | `lib/agents/market-data.ts` | ALTA — dados não oficiais podem ser lidos como reais |
| Curva patrimonial sintética com ruído determinístico | `lib/finance.ts` (`buildEquityCurve`, `seededNoise`) | ALTA — gráfico exibe história que não aconteceu |
| Open Finance simulado (bancos fictícios) | `lib/api/open-finance.ts` | MÉDIA — rotulado como simulado na UI, manter rótulo |
| Cotações brapi.dev (agregador) | `lib/api/brapi.ts`, `app/api/quotes/route.ts` | MÉDIA — agregador não é fonte primária (viola SOURCE_REGISTRY) |
| "Pipeline multi-agente" com scores heurísticos locais | `lib/agents/engine.ts` | MÉDIA — apresenta análise sem fonte/data-base |

Conclusão: o app é um protótipo de UI de qualidade razoável, mas **nenhum dado
exibido hoje atende às regras do sistema** (fonte oficial + data-base + status).

## Decisão

1. Nada é destruído: os módulos atuais permanecem como protótipo.
2. Regra imediata (CLAUDE.md): todo dado simulado/heurístico deve estar
   visivelmente rotulado; proibido apresentá-lo como real.
3. Integração real: consumir a API do backend (`docs/INTEGRATION.md`) na Fase 3+
   do roadmap, substituindo `market-data.ts`, `buildEquityCurve` sintética e o
   score dos "agentes" por dados gold com fonte e data-base.

## Riscos de regressão

- `lib/store/app-store.tsx` persiste estado em localStorage — mudanças de schema
  exigem migração de estado.
- `app/api/quotes/route.ts` protege o token brapi server-side — se mantido no
  protótipo, nunca expor o token no client.
