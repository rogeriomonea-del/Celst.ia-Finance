# CLAUDE.md — Celst.ia-Finance (frontend do Investment Intelligence OS)

## Papel deste repositório

Frontend Next.js 14 (App Router) + TypeScript + Tailwind do Investment
Intelligence OS. O backend (dados oficiais, motor determinístico, screener,
API) vive em `Celest.ia-v2-Alpha` — o frontend NUNCA calcula indicadores
financeiros nem inventa dados.

## Comandos

```bash
npm install
npm run dev        # http://localhost:3000
npm run build
npm run typecheck  # tsc --noEmit
npm run lint
```

## Regras não negociáveis

1. **Nenhum dado simulado exibido como real.** O protótipo atual contém dados
   estáticos/heurísticos (`lib/agents/market-data.ts`, `buildEquityCurve`,
   Open Finance mock) — qualquer tela que os use deve exibir selo "SIMULADO".
   Ver `docs/REPOSITORY_AUDIT.md`.
2. Toda tela financeira mostra fonte, data-base, status e metodologia
   (contrato em `docs/INTEGRATION.md`).
3. Status (`indisponivel`, `prejuizo`, `dado_insuficiente`) nunca viram 0.
4. Agregadores (brapi.dev) não são fonte primária; a substituição pelos
   endpoints do backend é o caminho aprovado.
5. Nunca armazenar senhas de B3/corretora/banco; tokens só server-side.
6. Acessibilidade: componentes Radix, estados de carregamento/erro/vazio
   obrigatórios; tema claro/escuro.

## Definition of Done

typecheck + lint verdes; sem dado simulado sem selo; sem secret no bundle;
telas novas com fonte + data-base visíveis.
